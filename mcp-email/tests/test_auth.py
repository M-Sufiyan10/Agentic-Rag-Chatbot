import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from mcp_email.auth import BearerTokenMiddleware


async def _ok(request: Request) -> PlainTextResponse:
    return PlainTextResponse("reached")


def _client() -> httpx.AsyncClient:
    app = BearerTokenMiddleware(Starlette(routes=[Route("/mcp", _ok, methods=["POST"])]), "s3cret")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_missing_token_401() -> None:
    async with _client() as c:
        r = await c.post("/mcp")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized"}


async def test_wrong_token_401() -> None:
    async with _client() as c:
        r = await c.post("/mcp", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_wrong_scheme_401() -> None:
    async with _client() as c:
        r = await c.post("/mcp", headers={"Authorization": "Basic s3cret"})
    assert r.status_code == 401


async def test_right_token_passes() -> None:
    async with _client() as c:
        r = await c.post("/mcp", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    assert r.text == "reached"
