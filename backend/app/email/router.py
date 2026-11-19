"""Connect, test and disconnect the caller's mailbox."""

from typing import Annotated, Any

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.models import User
from app.db import get_session
from app.email.client import EmailServerClient, EmailServerError
from app.email.crypto import decrypt_password, encrypt_password
from app.email.deps import EmailClientDep, EmailKeyDep
from app.email.models import EmailAccount, EmailDraft
from app.email.schemas import ConnectionResult, EmailAccountIn, EmailAccountOut
from app.email.service import get_account

router = APIRouter(prefix="/email", tags=["email"], dependencies=[Depends(get_current_user)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]

_FIELDS = (
    "address",
    "username",
    "imap_host",
    "imap_port",
    "imap_security",
    "smtp_host",
    "smtp_port",
    "smtp_security",
)


def _out(account: EmailAccount) -> EmailAccountOut:
    return EmailAccountOut(
        **{f: getattr(account, f) for f in _FIELDS},
        has_password=bool(account.password_encrypted),
    )


def _resolve_password(body: EmailAccountIn, stored: EmailAccount | None, key: SecretStr) -> str:
    if body.password is not None:
        return body.password
    if stored is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A password is required.")
    try:
        return decrypt_password(stored.password_encrypted, key)
    except InvalidToken:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your saved mailbox password can't be read; enter it again.",
        ) from None


async def _check(
    client: EmailServerClient, body: EmailAccountIn, password: str
) -> ConnectionResult:
    account: dict[str, Any] = body.model_dump(exclude={"password"}) | {"password": password}
    try:
        result = await client.call("check_connection", {"account": account})
    except EmailServerError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The email service is unavailable."
        ) from None
    return ConnectionResult(ok=bool(result.get("ok")), error=result.get("error"))


@router.get("/account", response_model=EmailAccountOut)
async def get_email_account(
    session: SessionDep, user: CurrentUser, _client: EmailClientDep
) -> EmailAccountOut:
    account = await get_account(session, user.id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No mailbox connected")
    return _out(account)


@router.put("/account", response_model=EmailAccountOut)
async def put_email_account(
    body: EmailAccountIn,
    session: SessionDep,
    user: CurrentUser,
    client: EmailClientDep,
    key: EmailKeyDep,
) -> EmailAccountOut:
    """Verify the mailbox works, then save it (password encrypted)."""
    account = await get_account(session, user.id)
    password = _resolve_password(body, account, key)
    result = await _check(client, body, password)
    if not result.ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result.error or "Connection failed.")
    if account is None:
        account = EmailAccount(user_id=user.id)
        session.add(account)
    for field in _FIELDS:
        setattr(account, field, getattr(body, field))
    if body.password is not None:
        account.password_encrypted = encrypt_password(body.password, key)
    await session.commit()
    return _out(account)


@router.post("/account/test", response_model=ConnectionResult)
async def test_email_account(
    body: EmailAccountIn,
    session: SessionDep,
    user: CurrentUser,
    client: EmailClientDep,
    key: EmailKeyDep,
) -> ConnectionResult:
    """Try the settings without saving anything."""
    password = _resolve_password(body, await get_account(session, user.id), key)
    return await _check(client, body, password)


@router.delete("/account", status_code=204)
async def delete_email_account(
    session: SessionDep, user: CurrentUser, _client: EmailClientDep
) -> Response:
    """Disconnect: delete the account and cancel the user's pending drafts. Idempotent."""
    account = await get_account(session, user.id)
    if account is not None:
        await session.delete(account)
    await session.execute(
        update(EmailDraft)
        .where(EmailDraft.user_id == user.id, EmailDraft.status == "pending")
        .values(status="cancelled")
    )
    await session.commit()
    return Response(status_code=204)
