from __future__ import annotations

from kaloricke_tabulky_mcp.auth import _authorized, _is_protected_path


def test_authorized_accepts_matching_bearer_token() -> None:
    assert _authorized("Bearer expected-token", "expected-token")
    assert _authorized("bearer expected-token", "expected-token")


def test_authorized_rejects_missing_or_wrong_token() -> None:
    assert not _authorized(None, "expected-token")
    assert not _authorized("Basic expected-token", "expected-token")
    assert not _authorized("Bearer wrong-token", "expected-token")


def test_only_mcp_path_is_protected() -> None:
    assert _is_protected_path("/mcp", ("/mcp",))
    assert _is_protected_path("/mcp/session", ("/mcp",))
    assert not _is_protected_path("/health", ("/mcp",))
