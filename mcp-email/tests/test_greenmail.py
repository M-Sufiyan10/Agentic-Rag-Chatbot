import imaplib
import time
import uuid
from collections.abc import Callable

import pytest

from mcp_email.account import Account
from mcp_email.errors import MailError
from mcp_email.mailbox import check_imap, count_from, fetch_message, list_messages
from mcp_email.sender import check_smtp, send_message

from .conftest import greenmail_up, make_account

pytestmark = pytest.mark.skipif(not greenmail_up(), reason="GreenMail not running on :3143")

Factory = Callable[..., Account]


def seed(to: Account, from_addr: str, subject: str, body: str = "hello") -> None:
    sender = make_account(from_addr)
    send_message(sender, to=to.address, subject=subject, body=body, reply_to=None)
    # GreenMail delivers synchronously, but give it a beat to index.
    time.sleep(0.2)


def rnd() -> str:
    return f"s{uuid.uuid4().hex[:8]}@example.com"


def test_check_connection_ok(greenmail_account: Factory) -> None:
    acct = greenmail_account()
    check_imap(acct)
    check_smtp(acct)


def test_unreachable_server_raises_mail_error(greenmail_account: Factory) -> None:
    # GreenMail auto-creates users and accepts any password, so a wrong password can't be
    # provoked here; a closed port exercises the same connect-failure -> MailError mapping.
    acct = greenmail_account().model_copy(update={"imap_port": 1, "smtp_port": 1})
    with pytest.raises(MailError, match="Can't reach"):
        check_imap(acct)
    with pytest.raises(MailError, match="Can't reach"):
        check_smtp(acct)


def test_list_newest_first_and_filters(greenmail_account: Factory) -> None:
    acct = greenmail_account()
    a, b = rnd(), rnd()
    seed(acct, a, "one")
    seed(acct, b, "two")
    seed(acct, a, "three")
    msgs = list_messages(acct, unread_only=False, from_address=None, since_days=7, limit=10)
    assert [m.subject for m in msgs] == ["three", "two", "one"]
    only_a = list_messages(acct, unread_only=False, from_address=a, since_days=7, limit=10)
    assert {m.subject for m in only_a} == {"one", "three"}
    assert (
        len(list_messages(acct, unread_only=True, from_address=None, since_days=7, limit=10)) == 3
    )
    assert (
        len(list_messages(acct, unread_only=False, from_address=None, since_days=7, limit=2)) == 2
    )


def test_fetch_does_not_mark_read(greenmail_account: Factory) -> None:
    acct = greenmail_account()
    seed(acct, rnd(), "peek")
    first = list_messages(acct, unread_only=True, from_address=None, since_days=7, limit=10)[0]
    msg = fetch_message(acct, first.uid)
    assert msg is not None and msg.subject == "peek"
    again = list_messages(acct, unread_only=True, from_address=None, since_days=7, limit=10)
    assert first.uid in [m.uid for m in again]


def test_count_from_exact_address(greenmail_account: Factory) -> None:
    acct = greenmail_account()
    seed(acct, "sara@example.com", "hi")
    assert count_from(acct, "SARA@example.com") == 1
    assert count_from(acct, "ara@example.com") == 0


def test_send_reply_threads(greenmail_account: Factory) -> None:
    acct = greenmail_account()
    orig = greenmail_account()
    seed(acct, orig.address, "Question")
    uid = list_messages(acct, unread_only=False, from_address=None, since_days=7, limit=1)[0].uid
    fetched = fetch_message(acct, uid)
    assert fetched is not None and fetched.message_id
    send_message(acct, to=orig.address, subject="Question", body="answer", reply_to=fetched)
    time.sleep(0.2)
    got = list_messages(orig, unread_only=False, from_address=None, since_days=7, limit=5)
    assert len(got) == 1 and got[0].subject == "Re: Question"
    reply = fetch_message(orig, got[0].uid)
    assert reply is not None
    assert reply.references is not None and fetched.message_id in reply.references
    conn = imaplib.IMAP4("127.0.0.1", 3143)
    conn.login(orig.username, orig.password)
    conn.select("INBOX", readonly=True)
    _, data = conn.uid("FETCH", got[0].uid, "(BODY.PEEK[HEADER.FIELDS (IN-REPLY-TO)])")
    conn.logout()
    assert isinstance(data[0], tuple)
    assert fetched.message_id in data[0][1].decode()


def test_unknown_uid_returns_none(greenmail_account: Factory) -> None:
    assert fetch_message(greenmail_account(), "999999") is None
