from __future__ import annotations

from kaloricke_tabulky_mcp.account_registry import AccountRegistry
from kaloricke_tabulky_mcp.config import load_settings
from kaloricke_tabulky_mcp.main import create_app


def test_safe_health_contains_failed_auth_without_network_probe() -> None:
    settings = load_settings(
        {
            "KT_ACCOUNTS": "personal",
            "KT_ACCOUNT_PERSONAL_EMAIL": "user@example.com",
            "KT_ACCOUNT_PERSONAL_PASSWORD": "secret",
            "KT_DEFAULT_ACCOUNT": "personal",
        }
    )
    registry = AccountRegistry(settings)
    registry._auth_diagnostics["personal"] = {  # noqa: SLF001 - contract test
        "status": "error",
        "checked_at": "2026-05-18T00:00:00+00:00",
        "message": "Invalid credentials",
    }

    payload = registry.safe_health()

    assert payload["auth_diagnostics"]["personal"]["status"] == "error"
    assert registry._clients == {}  # noqa: SLF001 - /health must not create/login client


def test_streamable_http_is_stateless(monkeypatch) -> None:
    monkeypatch.setenv("KT_ACCOUNTS", "personal")
    monkeypatch.setenv("KT_ACCOUNT_PERSONAL_EMAIL", "user@example.com")
    monkeypatch.setenv("KT_ACCOUNT_PERSONAL_PASSWORD", "secret")
    monkeypatch.setenv("KT_DEFAULT_ACCOUNT", "personal")

    app = create_app()

    assert app.settings.stateless_http is True
