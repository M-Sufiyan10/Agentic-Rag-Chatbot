"""Bearer-token ASGI middleware guarding the MCP HTTP endpoint."""

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token.encode()

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                scheme, _, supplied = bytes(value).partition(b" ")
                return scheme.lower() == b"bearer" and hmac.compare_digest(
                    supplied.strip(), self._token
                )
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self._authorized(scope):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
