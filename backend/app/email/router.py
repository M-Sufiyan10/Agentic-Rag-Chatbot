"""Connect, test and disconnect the caller's mailbox."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Response, status
from langchain_core.runnables import RunnableConfig
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.models import User
from app.chat.deps import Graph, GraphDep
from app.chat.transcript import completed_turn_ids
from app.config import Settings
from app.conversations.service import get_owned_conversation
from app.db import get_session
from app.email.client import EmailServerClient, EmailServerError
from app.email.crypto import decrypt_password, encrypt_password
from app.email.deps import EmailClientDep, EmailKeyDep, get_settings
from app.email.models import EmailAccount, EmailDraft
from app.email.schemas import ConnectionResult, EmailAccountIn, EmailAccountOut, EmailDraftOut
from app.email.service import account_payload, get_account

router = APIRouter(prefix="/email", tags=["email"], dependencies=[Depends(get_current_user)])
conversation_router = APIRouter(prefix="/conversations", tags=["email"])

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


# --- drafts -----------------------------------------------------------------

_ALREADY_DONE = "This draft was already sent or cancelled."
_SERVICE_DOWN = "The email service is unavailable."
_BAD_PASSWORD = "The saved mailbox password can't be read; re-enter it in Email settings."


async def _completed_turns(graph: Graph, conversation_id: uuid.UUID) -> set[str]:
    config: RunnableConfig = {"configurable": {"thread_id": str(conversation_id)}}
    snapshot = await graph.aget_state(config)
    return completed_turn_ids(snapshot.values.get("messages", []))


async def _owned_draft(
    session: AsyncSession, graph: Graph, user: User, draft_id: uuid.UUID
) -> EmailDraft:
    """The caller's draft, or 404 (also when its turn did not complete)."""
    draft = await session.scalar(
        select(EmailDraft).where(EmailDraft.id == draft_id, EmailDraft.user_id == user.id)
    )
    if draft is None or str(draft.turn_id) not in await _completed_turns(
        graph, draft.conversation_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found")
    return draft


async def _set_status(session: AsyncSession, draft_id: uuid.UUID, **values: Any) -> None:
    await session.execute(update(EmailDraft).where(EmailDraft.id == draft_id).values(**values))
    await session.commit()


@conversation_router.get("/{conversation_id}/email-drafts", response_model=list[EmailDraftOut])
async def list_email_drafts(
    conversation_id: uuid.UUID, session: SessionDep, user: CurrentUser, graph: GraphDep
) -> list[EmailDraft]:
    """The conversation's drafts from completed turns, oldest first."""
    await get_owned_conversation(session, user.id, conversation_id)
    completed = await _completed_turns(graph, conversation_id)
    rows = await session.scalars(
        select(EmailDraft)
        .where(EmailDraft.conversation_id == conversation_id, EmailDraft.user_id == user.id)
        .order_by(EmailDraft.created_at, EmailDraft.id)
    )
    return [d for d in rows if str(d.turn_id) in completed]


@router.post("/drafts/{draft_id}/send", response_model=EmailDraftOut)
async def send_email_draft(
    draft_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    client: EmailClientDep,
    key: EmailKeyDep,
    graph: GraphDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> EmailDraft:
    """Send a pending draft once; concurrent or repeated calls cannot send twice."""
    draft = await _owned_draft(session, graph, user, draft_id)
    since = datetime.now(UTC) - timedelta(hours=1)
    sent_recently = await session.scalar(
        select(func.count())
        .select_from(EmailDraft)
        .where(
            EmailDraft.user_id == user.id,
            EmailDraft.status == "sent",
            EmailDraft.sent_at >= since,
        )
    )
    if (sent_recently or 0) >= settings.email_max_sends_per_hour:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Send limit reached: at most {settings.email_max_sends_per_hour} emails per hour.",
        )
    claimed = await session.scalar(
        update(EmailDraft)
        .where(
            EmailDraft.id == draft_id, EmailDraft.user_id == user.id, EmailDraft.status == "pending"
        )
        .values(status="sending")
        .returning(EmailDraft.id)
    )
    await session.commit()
    if claimed is None:
        raise HTTPException(status.HTTP_409_CONFLICT, _ALREADY_DONE)

    account = await get_account(session, user.id)
    if account is None:
        await _set_status(session, draft_id, status="cancelled")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No mailbox connected")
    try:
        payload = account_payload(account, key)
    except InvalidToken:
        await _set_status(session, draft_id, status="failed", error=_BAD_PASSWORD)
        await session.refresh(draft)
        return draft
    arguments = {
        "account": payload,
        "to": draft.to_address,
        "subject": draft.subject,
        "body": draft.body,
        "reply_to_uid": draft.reply_to_uid,
    }
    try:
        result = await client.call("send_email", arguments)
    except EmailServerError:
        await _set_status(session, draft_id, status="failed", error=_SERVICE_DOWN)
    else:
        if result.get("sent"):
            await _set_status(
                session, draft_id, status="sent", sent_at=datetime.now(UTC), error=None
            )
        else:
            await _set_status(
                session, draft_id, status="failed", error=result.get("error") or "Sending failed."
            )
    await session.refresh(draft)
    return draft


@router.post("/drafts/{draft_id}/cancel", response_model=EmailDraftOut)
async def cancel_email_draft(
    draft_id: uuid.UUID, session: SessionDep, user: CurrentUser, graph: GraphDep
) -> EmailDraft:
    """Discard a pending draft."""
    draft = await _owned_draft(session, graph, user, draft_id)
    cancelled = await session.scalar(
        update(EmailDraft)
        .where(
            EmailDraft.id == draft_id, EmailDraft.user_id == user.id, EmailDraft.status == "pending"
        )
        .values(status="cancelled")
        .returning(EmailDraft.id)
    )
    await session.commit()
    if cancelled is None:
        raise HTTPException(status.HTTP_409_CONFLICT, _ALREADY_DONE)
    await session.refresh(draft)
    return draft
