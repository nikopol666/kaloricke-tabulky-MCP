"""Account lookup and client lifecycle management."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .client import KalorickeTabulkyClient, KalorickeTabulkyError
from .config import ConfigError, Settings, _normalize_alias


class AccountRegistry:
    """Creates clients lazily and enforces read/write account rules."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._clients: dict[str, KalorickeTabulkyClient] = {}
        self._auth_diagnostics: dict[str, dict[str, Any]] = {}

    @property
    def settings(self) -> Settings:
        return self._settings

    def list_accounts(self) -> list[dict[str, Any]]:
        return [
            {
                "alias": alias,
                "configured": True,
                "email": credentials.safe_dict()["email"],
                "auth_diagnostic": self._auth_diagnostics.get(alias),
            }
            for alias, credentials in sorted(self._settings.accounts.items())
        ]

    def safe_health(self) -> dict[str, Any]:
        return {
            "configured_accounts": [
                {"alias": alias, "configured": True}
                for alias in sorted(self._settings.accounts)
            ],
            "default_account": self._settings.default_account,
            "auth_diagnostics": self._auth_diagnostics,
        }

    def get_read_client(self, account: str | None = None) -> KalorickeTabulkyClient:
        alias = self._resolve_read_alias(account)
        return self._get_client(alias)

    def get_write_client(self, account: str) -> KalorickeTabulkyClient:
        if not account:
            raise ConfigError("Write tools require explicit account")
        alias = _normalize_alias(account)
        if alias not in self._settings.accounts:
            raise ConfigError(f"Unknown account alias: {account}")
        return self._get_client(alias)

    async def check_auth(self, account: str) -> dict[str, Any]:
        client = self.get_write_client(account)
        try:
            result = await client.auth_check()
            diagnostic = {
                "status": "ok",
                "checked_at": datetime.now(UTC).isoformat(),
                "message": result.get("status", "ok"),
            }
        except KalorickeTabulkyError as err:
            diagnostic = {
                "status": "error",
                "checked_at": datetime.now(UTC).isoformat(),
                "message": str(err),
            }
        self._auth_diagnostics[client.alias] = diagnostic
        return {"account": client.alias, **diagnostic}

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    def _resolve_read_alias(self, account: str | None) -> str:
        if account:
            alias = _normalize_alias(account)
        elif self._settings.default_account:
            alias = self._settings.default_account
        else:
            raise ConfigError("Set account or KT_DEFAULT_ACCOUNT for read/search tools")
        if alias not in self._settings.accounts:
            raise ConfigError(f"Unknown account alias: {account}")
        return alias

    def _get_client(self, alias: str) -> KalorickeTabulkyClient:
        existing = self._clients.get(alias)
        if existing is not None and not existing.closed:
            return existing
        credentials = self._settings.accounts[alias]
        client = KalorickeTabulkyClient(
            credentials, request_timeout=self._settings.request_timeout
        )
        self._clients[alias] = client
        return client

