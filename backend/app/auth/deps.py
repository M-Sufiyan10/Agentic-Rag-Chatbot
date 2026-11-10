"""FastAPI dependencies for authentication."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.security import InvalidTokenError, decode_token
from app.config import Settings
from app.db import get_session

_bearer = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    """Return the settings the app was created with (``app.state.settings``)."""
    settings: Settings = request.app.state.settings
    return settings


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> User:
    """Resolve the user from the ``Authorization: Bearer`` access token, or raise 401."""
    if credentials is None:
        raise _unauthorized()
    try:
        user_id = decode_token(credentials.credentials, "access", settings)
    except InvalidTokenError:
        raise _unauthorized("Invalid token") from None
    user = await session.get(User, user_id)
    if user is None:
        raise _unauthorized("Invalid token")
    return user
