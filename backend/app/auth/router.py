"""Auth endpoints: register, login, refresh, logout and me."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_app_settings, get_current_user
from app.auth.models import User
from app.auth.schemas import LoginIn, RegisterIn, TokenOut, UserOut
from app.auth.security import (
    InvalidTokenError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.config import Settings
from app.db import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
_COOKIE_PATH = "/api/auth"

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def _token_response(user: User, response: Response, settings: Settings) -> TokenOut:
    """Build the access-token body and (re)set the refresh cookie."""
    response.set_cookie(
        REFRESH_COOKIE,
        create_token(user.id, "refresh", settings),
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=_COOKIE_PATH,
    )
    return TokenOut(
        access_token=create_token(user.id, "access", settings),
        user=UserOut(id=user.id, email=user.email),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn, response: Response, session: SessionDep, settings: SettingsDep
) -> TokenOut:
    email = body.email.lower()
    user = User(email=email, password_hash=hash_password(body.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    return _token_response(user, response, settings)


@router.post("/login")
async def login(
    body: LoginIn, response: Response, session: SessionDep, settings: SettingsDep
) -> TokenOut:
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return _token_response(user, response, settings)


@router.post("/refresh")
async def refresh(
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> TokenOut:
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if refresh_token is None:
        raise invalid
    try:
        user_id = decode_token(refresh_token, "refresh", settings)
    except InvalidTokenError:
        raise invalid from None
    user = await session.get(User, user_id)
    if user is None:
        raise invalid
    return _token_response(user, response, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, settings: SettingsDep) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


@router.get("/me")
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return UserOut(id=user.id, email=user.email)
