from __future__ import annotations

import pytest

from kaloricke_tabulky_mcp.config import ConfigError, load_settings


def test_loads_multiple_accounts_and_default() -> None:
    settings = load_settings(
        {
            "KT_ACCOUNTS": "personal,partner",
            "KT_ACCOUNT_PERSONAL_EMAIL": "user@example.com",
            "KT_ACCOUNT_PERSONAL_PASSWORD": "secret-one",
            "KT_ACCOUNT_PARTNER_EMAIL": "partner@example.com",
            "KT_ACCOUNT_PARTNER_PASSWORD": "secret-two",
            "KT_DEFAULT_ACCOUNT": "personal",
            "MCP_BEARER_TOKEN": " mcp-secret ",
        }
    )

    assert sorted(settings.accounts) == ["partner", "personal"]
    assert settings.default_account == "personal"
    assert settings.accounts["personal"].safe_dict()["email"] == "u***@example.com"
    assert settings.mcp_bearer_token == "mcp-secret"


def test_strips_account_email_but_preserves_password() -> None:
    settings = load_settings(
        {
            "KT_ACCOUNTS": "personal",
            "KT_ACCOUNT_PERSONAL_EMAIL": " user@example.com\n",
            "KT_ACCOUNT_PERSONAL_PASSWORD": " secret ",
        }
    )

    assert settings.accounts["personal"].email == "user@example.com"
    assert settings.accounts["personal"].password == " secret "


def test_rejects_alias_collision() -> None:
    with pytest.raises(ConfigError, match="collide"):
        load_settings(
            {
                "KT_ACCOUNTS": "my-account,my_account",
                "KT_ACCOUNT_MY_ACCOUNT_EMAIL": "user@example.com",
                "KT_ACCOUNT_MY_ACCOUNT_PASSWORD": "secret",
            }
        )


def test_default_account_must_exist() -> None:
    with pytest.raises(ConfigError, match="KT_DEFAULT_ACCOUNT"):
        load_settings(
            {
                "KT_ACCOUNTS": "personal",
                "KT_ACCOUNT_PERSONAL_EMAIL": "user@example.com",
                "KT_ACCOUNT_PERSONAL_PASSWORD": "secret",
                "KT_DEFAULT_ACCOUNT": "missing",
            }
        )
