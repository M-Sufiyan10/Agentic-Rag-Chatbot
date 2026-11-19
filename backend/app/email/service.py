"""Email account lookup and the MCP ``account`` payload."""

import uuid
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.email.crypto import decrypt_password
from app.email.models import EmailAccount


async def get_account(session: AsyncSession, user_id: uuid.UUID) -> EmailAccount | None:
    result = await session.execute(select(EmailAccount).where(EmailAccount.user_id == user_id))
    return result.scalar_one_or_none()


def account_payload(account: EmailAccount, key: SecretStr) -> dict[str, Any]:
    """The MCP ``account`` argument, with the password decrypted. Never log or show it."""
    return {
        "address": account.address,
        "username": account.username,
        "password": decrypt_password(account.password_encrypted, key),
        "imap_host": account.imap_host,
        "imap_port": account.imap_port,
        "imap_security": account.imap_security,
        "smtp_host": account.smtp_host,
        "smtp_port": account.smtp_port,
        "smtp_security": account.smtp_security,
    }
