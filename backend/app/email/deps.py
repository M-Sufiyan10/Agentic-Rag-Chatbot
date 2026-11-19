"""Dependencies for the email routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from pydantic import SecretStr

from app.email.client import EmailServerClient


def get_email_client(request: Request) -> EmailServerClient:
    client: EmailServerClient | None = request.app.state.email_client
    if client is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Email is not configured on this server."
        )
    return client


def get_email_key(request: Request) -> SecretStr:
    key: SecretStr | None = request.app.state.settings.email_encryption_key
    if key is None:  # unreachable when a client exists, since both need email enabled
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Email is not configured on this server."
        )
    return key


EmailClientDep = Annotated[EmailServerClient, Depends(get_email_client)]
EmailKeyDep = Annotated[SecretStr, Depends(get_email_key)]
