import email.utils
import smtplib
from email.message import EmailMessage as PyMessage

from .account import Account
from .errors import MailError
from .mailbox import EmailMessage

TIMEOUT = 20


def _connect(account: Account) -> smtplib.SMTP:
    host, port = account.smtp_host, account.smtp_port
    try:
        if account.smtp_security == "ssl":
            conn: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT)
        else:
            conn = smtplib.SMTP(host, port, timeout=TIMEOUT)
            if account.smtp_security == "starttls":
                conn.starttls()
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"Can't reach {host}:{port}") from exc
    try:
        conn.login(account.username, account.password)
    except smtplib.SMTPAuthenticationError as exc:
        conn.close()
        raise MailError("SMTP login failed: wrong username or password") from exc
    except smtplib.SMTPNotSupportedError:
        pass  # server offers no AUTH; proceed unauthenticated
    except (OSError, smtplib.SMTPException) as exc:
        conn.close()
        raise MailError(f"SMTP login failed: {exc}") from exc
    return conn


def check_smtp(account: Account) -> None:
    conn = _connect(account)
    try:
        conn.quit()
    except (OSError, smtplib.SMTPException):
        conn.close()


def send_message(
    account: Account, *, to: str, subject: str, body: str, reply_to: EmailMessage | None
) -> str:
    msg = PyMessage()
    msg["From"] = account.address
    msg["To"] = to
    msg["Date"] = email.utils.formatdate(localtime=True)
    message_id = email.utils.make_msgid(domain=account.address.rsplit("@", 1)[-1])
    msg["Message-ID"] = message_id
    if reply_to is not None:
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        if reply_to.message_id:
            msg["In-Reply-To"] = reply_to.message_id
            refs = f"{reply_to.references} " if reply_to.references else ""
            msg["References"] = refs + reply_to.message_id
    msg["Subject"] = subject
    msg.set_content(body)
    conn = _connect(account)
    try:
        conn.send_message(msg)
        conn.quit()
    except (OSError, smtplib.SMTPException) as exc:
        conn.close()
        raise MailError(f"Couldn't send the email: {exc}") from exc
    return message_id
