import email
import email.policy
import email.utils
import imaplib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage as PyMessage
from html.parser import HTMLParser

from .account import Account
from .errors import MailError

TIMEOUT = 20
MAX_BODY = 8000
TRUNC_NOTE = "\n[... message truncated ...]"
SNIPPET_LEN = 200


@dataclass
class EmailSummary:
    uid: str
    from_name: str
    from_address: str
    subject: str
    date: str
    unread: bool
    snippet: str


@dataclass
class EmailMessage:
    uid: str
    from_name: str
    from_address: str
    to: list[str]
    date: str
    subject: str
    message_id: str | None
    references: str | None
    body: str
    truncated: bool
    attachments: list[str]


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    p.close()
    return re.sub(r"\s+", " ", "".join(p.parts)).strip()


def _header(msg: PyMessage, name: str) -> str:
    val = msg[name]
    return str(val).strip() if val is not None else ""


def _sender(msg: PyMessage) -> tuple[str, str]:
    name, addr = email.utils.parseaddr(_header(msg, "From"))
    return name, addr.lower()


def _body_text(msg: PyMessage) -> str:
    part = msg.get_body(preferencelist=("plain",))
    if part is not None:
        return str(part.get_content())
    part = msg.get_body(preferencelist=("html",))
    if part is not None:
        return _html_to_text(str(part.get_content()))
    return ""


def parse_message(uid: str, raw: bytes, *, unread: bool = False) -> EmailMessage:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, PyMessage)
    name, addr = _sender(msg)
    body = _body_text(msg)
    truncated = len(body) > MAX_BODY
    if truncated:
        body = body[:MAX_BODY] + TRUNC_NOTE
    attachments = [fn for p in msg.iter_attachments() if (fn := p.get_filename()) is not None]
    to_hdr = _header(msg, "To")
    return EmailMessage(
        uid=uid,
        from_name=name,
        from_address=addr,
        to=[a for _, a in email.utils.getaddresses([to_hdr]) if a],
        date=_header(msg, "Date"),
        subject=_header(msg, "Subject"),
        message_id=_header(msg, "Message-ID") or None,
        references=_header(msg, "References") or None,
        body=body,
        truncated=truncated,
        attachments=attachments,
    )


def _connect(account: Account) -> imaplib.IMAP4:
    host, port = account.imap_host, account.imap_port
    try:
        if account.imap_security == "ssl":
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, timeout=TIMEOUT)
        else:
            conn = imaplib.IMAP4(host, port, timeout=TIMEOUT)
            if account.imap_security == "starttls":
                conn.starttls()
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailError(f"Can't reach {host}:{port}") from exc
    try:
        conn.login(account.username, account.password)
    except imaplib.IMAP4.error as exc:
        _quiet_close(conn)
        raise MailError("IMAP login failed: wrong username or password") from exc
    except OSError as exc:
        _quiet_close(conn)
        raise MailError(f"Can't reach {host}:{port}") from exc
    return conn


def _quiet_close(conn: imaplib.IMAP4) -> None:
    try:
        conn.shutdown()
    except Exception:  # noqa: BLE001
        pass


class _Session:
    """Connects, selects INBOX read-only, and maps protocol errors to MailError."""

    def __init__(self, account: Account) -> None:
        self.account = account
        self.conn: imaplib.IMAP4 | None = None

    def __enter__(self) -> imaplib.IMAP4:
        self.conn = _connect(self.account)
        try:
            typ, _ = self.conn.select("INBOX", readonly=True)
        except (OSError, imaplib.IMAP4.error) as exc:
            _quiet_close(self.conn)
            raise MailError(f"Couldn't open the inbox: {exc}") from exc
        if typ != "OK":
            _quiet_close(self.conn)
            raise MailError("Couldn't open the inbox")
        return self.conn

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> None:
        assert self.conn is not None
        try:
            self.conn.logout()
        except Exception:  # noqa: BLE001
            _quiet_close(self.conn)
        if isinstance(exc, (OSError, imaplib.IMAP4.error)) and not isinstance(exc, MailError):
            raise MailError(f"IMAP error: {exc}") from exc


def check_imap(account: Account) -> None:
    with _Session(account):
        pass


def _uid_search(conn: imaplib.IMAP4, *criteria: str) -> list[str]:
    typ, data = conn.uid("SEARCH", *criteria)
    if typ != "OK" or not data or data[0] is None:
        return []
    raw = data[0]
    assert isinstance(raw, bytes)
    return raw.decode().split()


def _fetch_parts(conn: imaplib.IMAP4, uid: str, spec: str) -> tuple[bytes, bytes] | None:
    """Returns (flags+envelope response line, literal body) for one UID, or None."""
    typ, data = conn.uid("FETCH", uid, spec)
    if typ != "OK" or not data:
        return None
    for item in data:
        if isinstance(item, tuple):
            return item[0], item[1]
    return None


def _is_unread(meta: bytes) -> bool:
    return b"\\Seen" not in meta


def _snippet(body: str) -> str:
    return re.sub(r"\s+", " ", body).strip()[:SNIPPET_LEN]


def _summarize(conn: imaplib.IMAP4, uid: str) -> EmailSummary | None:
    got = _fetch_parts(conn, uid, "(FLAGS BODY.PEEK[])")
    if got is None:
        return None
    meta, raw = got
    m = parse_message(uid, raw)
    return EmailSummary(
        uid=uid,
        from_name=m.from_name,
        from_address=m.from_address,
        subject=m.subject,
        date=m.date,
        unread=_is_unread(meta),
        snippet=_snippet(m.body),
    )


def _imap_date(days: int) -> str:
    d = datetime.now() - timedelta(days=days)
    return d.strftime("%d-") + d.strftime("%b") + d.strftime("-%Y")


def _matching_uids(
    conn: imaplib.IMAP4, *, unread_only: bool, from_address: str | None, since_days: int | None
) -> list[str]:
    crit: list[str] = []
    if since_days is not None:
        crit += ["SINCE", _imap_date(since_days)]
    if unread_only:
        crit.append("UNSEEN")
    if from_address:
        crit += ["FROM", f'"{from_address}"']
    uids = _uid_search(conn, *(crit or ["ALL"]))
    return sorted(uids, key=int, reverse=True)


def list_messages(
    account: Account,
    *,
    unread_only: bool,
    from_address: str | None,
    since_days: int,
    limit: int,
) -> list[EmailSummary]:
    target = from_address.lower() if from_address else None
    out: list[EmailSummary] = []
    with _Session(account) as conn:
        uids = _matching_uids(
            conn, unread_only=unread_only, from_address=target, since_days=since_days
        )
        for uid in uids:
            if len(out) >= limit:
                break
            s = _summarize(conn, uid)
            if s is None:
                continue
            if target and s.from_address != target:
                continue
            out.append(s)
    return out


def fetch_message(account: Account, uid: str) -> EmailMessage | None:
    if not uid.isdigit():
        return None
    with _Session(account) as conn:
        got = _fetch_parts(conn, uid, "(FLAGS BODY.PEEK[])")
        if got is None:
            return None
        meta, raw = got
        return parse_message(uid, raw, unread=_is_unread(meta))


def count_from(account: Account, address: str) -> int:
    target = address.lower()
    n = 0
    with _Session(account) as conn:
        for uid in _uid_search(conn, "FROM", f'"{target}"'):
            got = _fetch_parts(conn, uid, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            if got is None:
                continue
            msg = email.message_from_bytes(got[1], policy=email.policy.default)
            if email.utils.parseaddr(str(msg["From"] or ""))[1].lower() == target:
                n += 1
    return n


__all__ = [
    "EmailMessage",
    "EmailSummary",
    "check_imap",
    "count_from",
    "fetch_message",
    "list_messages",
    "parse_message",
]
