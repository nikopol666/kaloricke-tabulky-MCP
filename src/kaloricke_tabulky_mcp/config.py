"""Environment configuration for Kaloricke Tabulky MCP."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .client import AccountCredentials


class ConfigError(ValueError):
    """Raised for invalid environment configuration."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with parsed account credentials."""

    accounts: dict[str, AccountCredentials]
    default_account: str | None
    mcp_server_name: str
    mcp_host: str
    mcp_port: int
    mcp_bearer_token: str | None
    request_timeout: int
    max_retries: int
    log_level: str


class RawSettings(BaseSettings):
    """Non-account settings loaded through pydantic-settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    kt_accounts: str = Field(default="", description="Comma-separated account aliases")
    kt_default_account: str | None = Field(default=None)
    mcp_server_name: str = Field(default="Kaloricke Tabulky MCP")
    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=8080)
    mcp_bearer_token: str | None = Field(default=None)
    request_timeout: int = Field(default=30)
    max_retries: int = Field(default=3)
    log_level: str = Field(default="INFO")


def load_settings(env: dict[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    raw = RawSettings(_env_file=None) if env is not None else RawSettings()
    if env is not None:
        raw = RawSettings.model_validate(
            {
                "kt_accounts": source.get("KT_ACCOUNTS", ""),
                "kt_default_account": source.get("KT_DEFAULT_ACCOUNT"),
                "mcp_server_name": source.get("MCP_SERVER_NAME", "Kaloricke Tabulky MCP"),
                "mcp_host": source.get("MCP_HOST", "0.0.0.0"),
                "mcp_port": int(source.get("MCP_PORT", "8080")),
                "mcp_bearer_token": source.get("MCP_BEARER_TOKEN"),
                "request_timeout": int(source.get("REQUEST_TIMEOUT", "30")),
                "max_retries": int(source.get("MAX_RETRIES", "3")),
                "log_level": source.get("LOG_LEVEL", "INFO"),
            }
        )
    accounts = _parse_accounts(source, raw.kt_accounts)
    default_account = _normalize_alias(raw.kt_default_account) if raw.kt_default_account else None
    if default_account and default_account not in accounts:
        raise ConfigError("KT_DEFAULT_ACCOUNT must match one configured account alias")
    return Settings(
        accounts=accounts,
        default_account=default_account,
        mcp_server_name=raw.mcp_server_name,
        mcp_host=raw.mcp_host,
        mcp_port=raw.mcp_port,
        mcp_bearer_token=_clean_optional_secret(raw.mcp_bearer_token),
        request_timeout=raw.request_timeout,
        max_retries=raw.max_retries,
        log_level=raw.log_level,
    )


def _parse_accounts(source: dict[str, str] | os._Environ[str], raw_aliases: str) -> dict[str, AccountCredentials]:
    aliases = [alias.strip() for alias in raw_aliases.split(",") if alias.strip()]
    if not aliases:
        raise ConfigError("KT_ACCOUNTS must list at least one account alias")

    normalized_seen: dict[str, str] = {}
    accounts: dict[str, AccountCredentials] = {}
    for alias in aliases:
        normalized = _normalize_alias(alias)
        env_suffix = _env_suffix(normalized)
        previous = normalized_seen.get(env_suffix)
        if previous is not None:
            raise ConfigError(
                f"Account aliases {previous!r} and {alias!r} collide after env normalization"
            )
        normalized_seen[env_suffix] = alias
        email = _clean_required_secret(source.get(f"KT_ACCOUNT_{env_suffix}_EMAIL"))
        password = _clean_required_secret(source.get(f"KT_ACCOUNT_{env_suffix}_PASSWORD"))
        if not email or not password:
            raise ConfigError(
                f"Missing KT_ACCOUNT_{env_suffix}_EMAIL or KT_ACCOUNT_{env_suffix}_PASSWORD"
            )
        accounts[normalized] = AccountCredentials(
            alias=normalized,
            email=email,
            password=password,
        )
    return accounts


def _normalize_alias(value: str | None) -> str:
    if value is None:
        raise ConfigError("Account alias is required")
    alias = value.strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z0-9_]+", alias):
        raise ConfigError(f"Invalid account alias: {value!r}")
    return alias


def _env_suffix(alias: str) -> str:
    return alias.upper()


def _clean_optional_secret(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _clean_required_secret(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
