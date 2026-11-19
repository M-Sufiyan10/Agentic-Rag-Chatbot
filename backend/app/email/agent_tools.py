"""The agent's email tools: list, read and draft (never send).

Like ``search_documents``, the user is never a tool argument: ``ToolNode`` injects
the run config and ``user_id`` is read from ``config["configurable"]["user_id"]``.
The mailbox ``account`` (with its decrypted password) is built here and sent to the
email MCP server through ``EmailServerClient.call``, which is called without the run
config or callbacks, so tracing never records it.
"""

import logging
import uuid
from typing import Any

from cryptography.fernet import InvalidToken
from email_validator import EmailNotValidError, validate_email
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.email.client import EmailServerClient, EmailServerError
from app.email.models import EmailDraft
from app.email.service import account_payload, get_account

logger = logging.getLogger(__name__)

NO_MAILBOX = "No mailbox is connected. Ask the user to connect one in Email settings."
UNAVAILABLE = "Error: the email service is unavailable right now. Tell the user."
NOT_LISTED = (
    "Not drafted: {to} has never emailed this mailbox. "
    "I can only draft emails to people who have emailed the user."
)
DRAFTED = "Draft saved. It has NOT been sent: the user must review it and click Send in the chat."
BAD_PASSWORD = (
    "The saved mailbox password can't be read. Ask the user to re-enter it in Email settings."
)


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def make_email_tools(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    client: EmailServerClient,
    settings: Settings,
) -> list[BaseTool]:
    async def account_for(config: RunnableConfig) -> tuple[dict[str, Any] | None, str | None]:
        """The MCP account payload for the run's user, or ``(None, message)``."""
        user_id = _uuid((config.get("configurable") or {}).get("user_id"))
        if user_id is None or settings.email_encryption_key is None:
            return None, UNAVAILABLE
        try:
            async with sessionmaker() as session:
                account = await get_account(session, user_id)
            if account is None:
                return None, NO_MAILBOX
            return account_payload(account, settings.email_encryption_key), None
        except InvalidToken:
            return None, BAD_PASSWORD
        except Exception:
            logger.exception("Could not load the user's mailbox")
            return None, UNAVAILABLE

    async def call(
        config: RunnableConfig, name: str, arguments: dict[str, Any]
    ) -> tuple[Any, str | None]:
        payload, message = await account_for(config)
        if payload is None:
            return None, message
        try:
            # Deliberately no config/callbacks: the account must never reach tracing.
            return await client.call(name, {"account": payload, **arguments}), None
        except EmailServerError:
            return None, UNAVAILABLE

    @tool
    async def list_emails(
        config: RunnableConfig,
        unread_only: bool = False,
        from_address: str | None = None,
        since_days: int = 7,
        limit: int = 10,
    ) -> str:
        """List emails in the user's inbox, newest first.

        Use for questions about their email, to summarize it, or to find an email.
        """
        result, message = await call(
            config,
            "list_emails",
            {
                "unread_only": unread_only,
                "from_address": from_address,
                "since_days": since_days,
                "limit": limit,
            },
        )
        return message if message is not None else str(result)

    @tool
    async def read_email(uid: str, config: RunnableConfig) -> str:
        """Read one email in full by its uid from list_emails."""
        result, message = await call(config, "read_email", {"uid": uid})
        return message if message is not None else str(result)

    @tool
    async def draft_email(
        to: str,
        subject: str,
        body: str,
        config: RunnableConfig,
        reply_to_uid: str | None = None,
    ) -> str:
        """Draft an email or reply for the user to review.

        Only to someone who has emailed them. It is NOT sent until the user clicks Send.
        """
        configurable = config.get("configurable") or {}
        user_id = _uuid(configurable.get("user_id"))
        turn_id = _uuid(configurable.get("turn_id"))
        conversation_id = _uuid(configurable.get("thread_id"))
        if user_id is None or turn_id is None or conversation_id is None:
            return UNAVAILABLE
        try:
            address = validate_email(to, check_deliverability=False).normalized
        except EmailNotValidError:
            return f"Not drafted: {to} is not a valid email address."
        result, message = await call(config, "is_listed_sender", {"address": address})
        if message is not None:
            return message
        if not isinstance(result, dict) or result.get("error"):
            return UNAVAILABLE
        if not result.get("listed"):
            return NOT_LISTED.format(to=address)
        try:
            async with sessionmaker() as session:
                session.add(
                    EmailDraft(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        to_address=address,
                        subject=subject,
                        body=body,
                        reply_to_uid=reply_to_uid,
                        status="pending",
                    )
                )
                await session.commit()
        except Exception:
            logger.exception("Could not save the email draft")
            return UNAVAILABLE
        return DRAFTED

    return [list_emails, read_email, draft_email]
