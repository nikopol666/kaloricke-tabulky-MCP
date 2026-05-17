"""HTTP bearer-token protection for streamable MCP requests."""

from __future__ import annotations

from secrets import compare_digest
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenMiddleware:
    """Protect selected HTTP paths with a static bearer token."""

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        protected_paths: tuple[str, ...] = ("/mcp",),
    ) -> None:
        self.app = app
        self.token = token
        self.protected_paths = protected_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_protected_path(scope.get("path", ""), self.protected_paths):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        if _authorized(headers.get("authorization"), self.token):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {"error": "unauthorized", "message": "Missing or invalid bearer token"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)


def _authorized(header_value: str | None, expected_token: str) -> bool:
    if not header_value:
        return False
    scheme, separator, token = header_value.partition(" ")
    return (
        separator == " "
        and scheme.lower() == "bearer"
        and compare_digest(token.strip(), expected_token)
    )


def _is_protected_path(path: str, protected_paths: tuple[str, ...]) -> bool:
    return any(
        path == protected_path or path.startswith(f"{protected_path}/")
        for protected_path in protected_paths
    )
