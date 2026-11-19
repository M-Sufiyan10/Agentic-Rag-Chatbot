import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.models import User
from app.config import Settings
from app.conversations.models import Conversation
from app.email.agent_tools import DRAFTED, NO_MAILBOX, NOT_LISTED, UNAVAILABLE, make_email_tools
from app.email.client import EmailServerError
from app.email.crypto import encrypt_password
from app.email.models import EmailAccount, EmailDraft
from app.main import email_tools_for
from tests.conftest import TEST_DATABASE_URL
from tests.email_mcp.fakes import FakeEmailClient

KEY = SecretStr(Fernet.generate_key().decode())
PASSWORD = "pw-plain-secret"


@pytest.fixture
async def sm() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def settings(test_settings: Settings) -> Settings:
    return test_settings.model_copy(update={"email_encryption_key": KEY})


def _tools(
    sm: async_sessionmaker[AsyncSession], client: FakeEmailClient, settings: Settings
) -> dict[str, BaseTool]:
    tools = make_email_tools(sessionmaker=sm, client=client, settings=settings)  # type: ignore[arg-type]
    return {t.name: t for t in tools}


async def _user(sm: async_sessionmaker[AsyncSession], with_account: bool = True) -> tuple[Any, Any]:
    async with sm() as session:
        user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        conv = Conversation(user_id=user.id)
        session.add(conv)
        if with_account:
            session.add(
                EmailAccount(
                    user_id=user.id,
                    address="me@example.com",
                    username="me",
                    password_encrypted=encrypt_password(PASSWORD, KEY),
                    imap_host="imap.example.com",
                    imap_port=993,
                    imap_security="ssl",
                    smtp_host="smtp.example.com",
                    smtp_port=587,
                    smtp_security="starttls",
                )
            )
        await session.commit()
        return user.id, conv.id


def _cfg(user_id: Any, conv_id: Any = None, turn_id: Any = None) -> Any:
    configurable: dict[str, Any] = {"user_id": str(user_id)}
    if conv_id is not None:
        configurable["thread_id"] = str(conv_id)
    if turn_id is not None:
        configurable["turn_id"] = str(turn_id)
    return {"configurable": configurable}


async def _drafts(sm: async_sessionmaker[AsyncSession]) -> list[EmailDraft]:
    async with sm() as session:
        return list((await session.scalars(select(EmailDraft))).all())


async def test_no_mailbox_message(sm: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    user_id, _ = await _user(sm, with_account=False)
    fake = FakeEmailClient()
    out = await _tools(sm, fake, settings)["list_emails"].ainvoke({}, config=_cfg(user_id))
    assert out == NO_MAILBOX
    assert fake.calls == []


async def test_list_emails_passes_account_and_args(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    user_id, _ = await _user(sm)
    fake = FakeEmailClient({"list_emails": "1 email text"})
    out = await _tools(sm, fake, settings)["list_emails"].ainvoke(
        {"unread_only": True, "from_address": "a@b.com", "since_days": 3, "limit": 5},
        config=_cfg(user_id),
    )
    assert out == "1 email text"
    ((name, args),) = fake.calls
    assert name == "list_emails"
    assert args["account"]["password"] == PASSWORD
    assert args["account"]["address"] == "me@example.com"
    assert {k: v for k, v in args.items() if k != "account"} == {
        "unread_only": True,
        "from_address": "a@b.com",
        "since_days": 3,
        "limit": 5,
    }


async def test_read_email_passes_uid(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    user_id, _ = await _user(sm)
    fake = FakeEmailClient({"read_email": "body"})
    out = await _tools(sm, fake, settings)["read_email"].ainvoke(
        {"uid": "42"}, config=_cfg(user_id)
    )
    assert out == "body"
    assert fake.calls[0][1]["uid"] == "42"


async def test_service_down_returns_unavailable(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    user_id, _ = await _user(sm)
    fake = FakeEmailClient({"list_emails": EmailServerError("down")})
    out = await _tools(sm, fake, settings)["list_emails"].ainvoke({}, config=_cfg(user_id))
    assert out == UNAVAILABLE


async def test_wrong_key_returns_readable_message(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    user_id, _ = await _user(sm)
    other = settings.model_copy(
        update={"email_encryption_key": SecretStr(Fernet.generate_key().decode())}
    )
    out = await _tools(sm, FakeEmailClient(), other)["list_emails"].ainvoke(
        {}, config=_cfg(user_id)
    )
    assert "re-enter it in Email settings" in out


async def test_draft_refused_when_not_listed(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    user_id, conv = await _user(sm)
    fake = FakeEmailClient({"is_listed_sender": {"listed": False, "count": 0}})
    out = await _tools(sm, fake, settings)["draft_email"].ainvoke(
        {"to": "stranger@example.com", "subject": "s", "body": "b"},
        config=_cfg(user_id, conv, uuid.uuid4()),
    )
    assert out == NOT_LISTED.format(to="stranger@example.com")
    assert await _drafts(sm) == []


async def test_draft_listed_check_error_is_not_a_refusal(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    user_id, conv = await _user(sm)
    fake = FakeEmailClient({"is_listed_sender": {"listed": False, "count": 0, "error": "imap"}})
    out = await _tools(sm, fake, settings)["draft_email"].ainvoke(
        {"to": "a@example.com", "subject": "s", "body": "b"},
        config=_cfg(user_id, conv, uuid.uuid4()),
    )
    assert out == UNAVAILABLE
    assert await _drafts(sm) == []


async def test_draft_saved_pending_with_turn_and_conversation(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    user_id, conv = await _user(sm)
    turn = uuid.uuid4()
    fake = FakeEmailClient({"is_listed_sender": {"listed": True, "count": 2}})
    out = await _tools(sm, fake, settings)["draft_email"].ainvoke(
        {"to": "friend@example.com", "subject": "Hi", "body": "Hello", "reply_to_uid": "7"},
        config=_cfg(user_id, conv, turn),
    )
    assert out == DRAFTED
    (draft,) = await _drafts(sm)
    assert (draft.user_id, draft.conversation_id, draft.turn_id) == (user_id, conv, turn)
    assert (draft.to_address, draft.subject, draft.body) == ("friend@example.com", "Hi", "Hello")
    assert draft.reply_to_uid == "7"
    assert draft.status == "pending"
    assert fake.calls[0][0] == "is_listed_sender"


async def test_draft_without_turn_id_unavailable(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    user_id, conv = await _user(sm)
    out = await _tools(sm, FakeEmailClient(), settings)["draft_email"].ainvoke(
        {"to": "a@example.com", "subject": "s", "body": "b"}, config=_cfg(user_id, conv)
    )
    assert out == UNAVAILABLE


async def test_draft_invalid_address(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    user_id, conv = await _user(sm)
    fake = FakeEmailClient()
    out = await _tools(sm, fake, settings)["draft_email"].ainvoke(
        {"to": "not-an-address", "subject": "s", "body": "b"},
        config=_cfg(user_id, conv, uuid.uuid4()),
    )
    assert out == "Not drafted: not-an-address is not a valid email address."
    assert fake.calls == []


class Recorder(BaseCallbackHandler):
    def __init__(self) -> None:
        self.seen: list[str] = []

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        self.seen += [repr(serialized), input_str, repr(kwargs.get("inputs"))]
        self.seen.append(repr({k: v for k, v in kwargs.items() if k != "inputs"}))


async def test_account_never_in_traced_inputs(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    user_id, _ = await _user(sm)
    fake = FakeEmailClient({"list_emails": "ok"})
    tools = _tools(sm, fake, settings)
    recorder = Recorder()
    node = ToolNode(list(tools.values()))
    msg = AIMessage(
        "",
        tool_calls=[{"id": "c1", "name": "list_emails", "args": {"limit": 3}, "type": "tool_call"}],
    )
    config = _cfg(user_id)
    config["callbacks"] = [recorder]
    graph = (
        StateGraph(MessagesState)
        .add_node("tools", node)
        .add_edge(START, "tools")
        .add_edge("tools", END)
        .compile()
    )
    result = await graph.ainvoke({"messages": [msg]}, config)
    tool_message = result["messages"][-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.content == "ok"
    assert recorder.seen
    joined = "\n".join(recorder.seen)
    assert PASSWORD not in joined
    assert "account" not in joined
    for t in tools.values():
        props = set(t.get_input_schema().model_json_schema().get("properties", {}))
        assert not props & {"account", "user_id", "config"}


async def test_tools_registered_only_when_email_enabled(
    sm: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    app = FastAPI()
    app.state.sessionmaker = sm
    app.state.settings = settings
    app.state.email_client = None
    assert email_tools_for(app) == []
    app.state.email_client = FakeEmailClient()
    assert {t.name for t in email_tools_for(app)} == {"list_emails", "read_email", "draft_email"}
