import ipaddress
import socket
from typing import Literal

from pydantic import BaseModel

Security = Literal["ssl", "starttls", "none"]


class Account(BaseModel):
    address: str
    username: str
    password: str
    imap_host: str
    imap_port: int
    imap_security: Security
    smtp_host: str
    smtp_port: int
    smtp_security: Security


class UnsafeAccountError(ValueError):
    pass


def _check_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise UnsafeAccountError(f"can't resolve host {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeAccountError(f"host {host} resolves to a non-public address")


def check_account_safety(account: Account, *, allow_insecure: bool) -> None:
    if allow_insecure:
        return
    if "none" in (account.imap_security, account.smtp_security):
        raise UnsafeAccountError("unencrypted connections (security 'none') are not allowed")
    _check_host(account.imap_host)
    _check_host(account.smtp_host)
