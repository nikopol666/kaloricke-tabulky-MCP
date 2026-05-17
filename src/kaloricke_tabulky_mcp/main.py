"""FastMCP server entrypoint for Kaloricke Tabulky."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .account_registry import AccountRegistry
from .config import ConfigError, load_settings
from .tools import setup_tools

logger = logging.getLogger(__name__)

registry: AccountRegistry | None = None


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    try:
        yield
    finally:
        if registry is not None:
            await registry.close()


def create_app() -> FastMCP:
    global registry
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    registry = AccountRegistry(settings)
    mcp = FastMCP(
        name=settings.mcp_server_name,
        instructions=(
            "MCP server for Kaloricke Tabulky food search and diary writes. "
            "Write tools require explicit account and commit=true."
        ),
        host=settings.mcp_host,
        port=settings.mcp_port,
        json_response=True,
        lifespan=lifespan,
    )
    setup_tools(mcp, registry)
    _add_health_route(mcp, registry)
    return mcp


def _add_health_route(mcp: FastMCP, account_registry: AccountRegistry) -> None:
    @mcp.custom_route("/health", methods=["GET"])
    async def http_health_check(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "healthy",
                "message": "Kaloricke Tabulky MCP process/config liveness is OK",
                "server_info": {
                    "name": account_registry.settings.mcp_server_name,
                    "port": account_registry.settings.mcp_port,
                    "transport": "streamable-http",
                    "endpoint": "/mcp",
                },
                **account_registry.safe_health(),
            },
            status_code=200,
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Kaloricke Tabulky MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport mode",
    )
    args = parser.parse_args()

    try:
        mcp = create_app()
    except ConfigError as err:
        raise SystemExit(f"Configuration error: {err}") from err

    if args.transport == "stdio":
        await mcp.run_stdio_async()
    else:
        await mcp.run_streamable_http_async()


def cli() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    cli()

