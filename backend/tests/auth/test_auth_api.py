"""Tests for the /api/auth endpoints."""

import httpx

from tests.conftest import register_user

PASSWORD = "correct-horse-1"


async def test_register_returns_token_and_sets_cookie(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/register", json={"email": "a@example.com", "password": PASSWORD}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "a@example.com"
    assert "refresh_token" in r.cookies


async def test_register_duplicate_email_case_insensitive(client: httpx.AsyncClient) -> None:
    await register_user(client, email="Bob@Example.com")
    r = await client.post(
        "/api/auth/register", json={"email": "bob@example.com", "password": PASSWORD}
    )
    assert r.status_code == 409
    assert r.json() == {"detail": "Email already registered"}


async def test_register_rejects_short_password(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/register", json={"email": "a@example.com", "password": "short"}
    )
    assert r.status_code == 422


async def test_login_success_any_email_case(client: httpx.AsyncClient) -> None:
    await register_user(client, email="bob@example.com")
    r = await client.post(
        "/api/auth/login", json={"email": "BOB@example.com", "password": PASSWORD}
    )
    assert r.status_code == 200
    assert "refresh_token" in r.cookies


async def test_login_wrong_password_and_unknown_email_same_401(client: httpx.AsyncClient) -> None:
    await register_user(client, email="bob@example.com")
    wrong = await client.post(
        "/api/auth/login", json={"email": "bob@example.com", "password": "wrong-password"}
    )
    unknown = await client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid email or password"}


async def test_me_requires_token(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


async def test_me_returns_current_user(client: httpx.AsyncClient) -> None:
    headers = await register_user(client)
    r = await client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "alice@example.com"


async def test_refresh_issues_new_access_token(client: httpx.AsyncClient) -> None:
    await register_user(client)
    await client.post("/api/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200


async def test_refresh_without_cookie_401(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401


async def test_access_token_cannot_be_used_as_refresh(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/register", json={"email": "a@example.com", "password": PASSWORD}
    )
    client.cookies.clear()
    client.cookies.set("refresh_token", r.json()["access_token"], path="/api/auth")
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_logout_clears_cookie_then_refresh_401(client: httpx.AsyncClient) -> None:
    await register_user(client)
    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.post("/api/auth/refresh")).status_code == 401
