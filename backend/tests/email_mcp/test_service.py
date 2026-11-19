import uuid

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.email.crypto import encrypt_password
from app.email.models import EmailAccount
from app.email.service import account_payload, get_account

KEY = SecretStr(Fernet.generate_key().decode())


async def _user(session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="x")
    session.add(user)
    await session.flush()
    return user


def _account(user_id: uuid.UUID) -> EmailAccount:
    return EmailAccount(
        user_id=user_id,
        address="me@example.com",
        username="me",
        password_encrypted=encrypt_password("pw-plain", KEY),
        imap_host="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_security="starttls",
    )


async def test_account_payload_decrypts(db_session: AsyncSession) -> None:
    user = await _user(db_session, "a@example.com")
    db_session.add(_account(user.id))
    await db_session.commit()
    account = await get_account(db_session, user.id)
    assert account is not None
    assert account_payload(account, KEY) == {
        "address": "me@example.com",
        "username": "me",
        "password": "pw-plain",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_security": "ssl",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
    }


async def test_get_account_scoped_to_user(db_session: AsyncSession) -> None:
    alice = await _user(db_session, "a@example.com")
    bob = await _user(db_session, "b@example.com")
    db_session.add(_account(alice.id))
    await db_session.commit()
    assert await get_account(db_session, alice.id) is not None
    assert await get_account(db_session, bob.id) is None
