import asyncio
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import date
import logging

from aiohttp import web
import pytest

from kaloricke_tabulky_mcp import client as module
from kaloricke_tabulky_mcp.client import (
    AccountCredentials,
    KalorickeTabulkyClient,
    KalorickeTabulkyError,
    SessionExpiredError,
    SummaryData,
    _session_expired_message,
)
from kaloricke_tabulky_mcp.config import load_settings
from kaloricke_tabulky_mcp.errors import safe_text
from kaloricke_tabulky_mcp.tools import setup_tools, _error
from test_tools_write_safety import FakeMCP, FakeRegistry, FakeClient


@pytest.fixture
async def auth(aiohttp_server, monkeypatch):
    state = SimpleNamespace(
        calls=Counter(),
        status=401,
        message="login required",
        always_expired=False,
        barrier=asyncio.Event(),
        threshold=0,
        business_error=None,
        response=None,
    )
    state.barrier.set()

    async def login_page(request):
        return web.Response(
            text='<input id="loginUrl" action="/login/create?format=json">'
        )

    async def login(request):
        state.calls["login"] += 1
        await asyncio.wait_for(state.barrier.wait(), 2)
        payload = await request.json()
        assert payload["password"] == state.client._credentials.login_password_hash()
        response = web.json_response({"code": 0})
        response.set_cookie("session", "fresh-cookie")
        return response

    async def data(request):
        state.calls["data"] += 1
        if request.cookies.get("session") != "fresh-cookie" or state.always_expired:
            state.calls["stale"] += 1
            if state.threshold and state.calls["stale"] >= state.threshold:
                state.barrier.set()
            if state.status == 200:
                return web.json_response({"code": 10, "message": state.message})
            return web.Response(
                status=state.status,
                text="Login required",
                headers={"Location": "/login"} if state.status == 302 else None,
            )
        if state.response is not None:
            return web.Response(text=state.response, content_type="text/html")
        if state.business_error is not None:
            return web.json_response({"code": 99, "message": state.business_error})
        return web.json_response({"code": 0, "data": {"ok": True}, "message": state.message})

    app = web.Application()
    app.router.add_get("/login", login_page)
    app.router.add_post("/login/create", login)
    app.router.add_get("/data", data)
    server = await aiohttp_server(app)
    base = str(server.make_url("/")).rstrip("/")
    monkeypatch.setattr(module, "BASE_URL", base + "/")
    monkeypatch.setattr(module, "LOGIN_PAGE_URL", base + "/login")
    monkeypatch.setattr(module, "LOGIN_URL", base + "/login/create?format=json")
    state.client = KalorickeTabulkyClient(
        AccountCredentials("personal", "fixture@example.com", "fixture-password")
    )
    state.client._cookies = "session=old-cookie"
    state.url = base + "/data"
    try:
        yield state
    finally:
        await state.client.close()


async def test_five_expired_requests_share_one_login(auth):
    auth.threshold = 5
    auth.barrier.clear()
    results = await asyncio.gather(
        *(auth.client._request_with_reauth("GET", auth.url) for _ in range(5))
    )
    assert all(row["data"]["ok"] for row in results)
    assert auth.calls["login"] == 1
    assert auth.calls["stale"] == 5
    assert auth.calls["data"] == 10


async def test_five_initial_authentication_calls_share_login(auth):
    auth.client._cookies = None
    await asyncio.gather(*(auth.client.authenticate() for _ in range(5)))
    assert auth.calls["login"] == 1


@pytest.mark.parametrize("status", [302, 401, 403, 200])
@pytest.mark.parametrize(
    "operation",
    ["_request_with_reauth", "_request_any_with_reauth", "_request_text_with_reauth"],
)
async def test_all_read_paths_reauthenticate_once(auth, status, operation):
    auth.status = status
    result = await getattr(auth.client, operation)("GET", auth.url)
    assert result
    assert auth.calls["login"] == 1
    assert auth.calls["data"] == 2


async def test_retry_is_limited_to_one_attempt(auth):
    auth.always_expired = True
    with pytest.raises(SessionExpiredError):
        await auth.client._request_with_reauth("GET", auth.url)
    assert auth.calls["login"] == 1
    assert auth.calls["data"] == 2


async def test_unrelated_business_error_does_not_reauthenticate(auth):
    auth.client._cookies = "session=fresh-cookie"
    auth.business_error = "Food was not found"
    with pytest.raises(KalorickeTabulkyError, match="Food was not found"):
        await auth.client._request_with_reauth("GET", auth.url)
    assert auth.calls["login"] == 0
    assert auth.calls["data"] == 1


async def test_successful_response_with_login_words_does_not_refresh(auth):
    auth.client._cookies = "session=fresh-cookie"
    result = await auth.client._request_with_reauth("GET", auth.url)
    assert result["code"] == 0
    assert result["message"] == "login required"
    assert auth.calls["login"] == 0


@pytest.mark.parametrize(
    "message",
    [
        "Please login to your account settings",
        "Login failed: incorrect password",
        "Food login bread not found",
    ],
)
def test_unrelated_login_mentions_are_not_session_expiry(message):
    assert not _session_expired_message(message)


@pytest.mark.parametrize(
    "message",
    ["Session expired", "Authentication required", "Přihlaste se", "Nejste přihlášen."],
)
def test_conservative_synthetic_login_message_allowlist(message):
    # These are synthetic contracts, not captures of a verified KT error string.
    assert _session_expired_message(message)


async def test_errors_are_bounded_and_debug_output_is_redacted(auth, caplog):
    auth.client._cookies = "session=fresh-cookie"
    digest = auth.client._credentials.login_password_hash()
    auth.business_error = (
        f"fixture-password {digest} fresh-cookie " + "x" * 1000 + "TAIL_MARKER"
    )
    with caplog.at_level(logging.DEBUG, logger=module.__name__):
        with pytest.raises(KalorickeTabulkyError) as exc:
            await auth.client._request_with_reauth("GET", auth.url)
    assert len(str(exc.value)) <= 300
    assert "dict" in str(exc.value)
    assert "application/json" in str(exc.value)
    assert "TAIL_MARKER" in caplog.text
    assert "TAIL_MARKER" not in str(exc.value)
    for secret in ("fixture-password", digest, "fresh-cookie"):
        assert secret not in str(exc.value)
        assert secret not in caplog.text


async def test_non_json_error_is_short_and_redacted(auth, caplog):
    auth.client._cookies = "session=fresh-cookie"
    auth.response = (
        '<html>password="unknown-password" Cookie="unknown-cookie" '
        + "x" * 1000
        + "</html>"
    )
    with caplog.at_level(logging.DEBUG, logger=module.__name__):
        with pytest.raises(KalorickeTabulkyError) as exc:
            await auth.client._request_with_reauth("GET", auth.url)
    assert len(str(exc.value)) <= 300
    assert "str" in str(exc.value)
    assert "text/html" in str(exc.value)
    assert "unknown-password" not in caplog.text
    assert "unknown-cookie" not in caplog.text


def test_generic_error_redacts_hashes_and_named_credentials():
    text = safe_text(
        {
            "password": "secret",
            "cookie": "session=unknown",
            "hash": "0123456789abcdef0123456789abcdef",
        }
    )
    assert "session=unknown" not in text
    assert "0123456789abcdef0123456789abcdef" not in text
    result = _error(ValueError("x" * 1000))
    assert len(result["error"]) <= 300


@pytest.mark.parametrize("enabled", [False, True])
def test_diagnostics_are_opt_in(enabled):
    registry = FakeRegistry()
    registry.settings.enable_diagnostics = enabled
    mcp = FakeMCP()
    setup_tools(mcp, registry)
    assert ("get_diary_detail" in mcp.tools) is enabled
    assert ("probe_user_meal_endpoints" in mcp.tools) is enabled
    assert "get_diary_entries" in mcp.tools
    assert "get_diary_range" in mcp.tools


def test_diagnostics_config_defaults_off():
    env = {
        "KT_ACCOUNTS": "personal",
        "KT_ACCOUNT_PERSONAL_EMAIL": "fixture@example.com",
        "KT_ACCOUNT_PERSONAL_PASSWORD": "fixture",
    }
    assert load_settings(env).enable_diagnostics is False
    assert (
        load_settings({**env, "KT_ENABLE_DIAGNOSTICS": "true"}).enable_diagnostics
        is True
    )


@pytest.mark.parametrize(
    "tool", ["record_food_batch", "prepare_recipe_import", "create_custom_recipe"]
)
async def test_item_resolution_is_parallel_bounded_and_ordered(auth, monkeypatch, tool):
    client = auth.client
    active = peak = 0
    gate = asyncio.Event()
    fake = FakeClient()

    async def resolve(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 4:
            gate.set()
        try:
            await asyncio.wait_for(gate.wait(), 2)
            # Yield completion independently of input order.
            if kwargs["food_guid"] == "food-0":
                await asyncio.sleep(0)
            return await fake.resolve_food(**kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(client, "_resolve_food", resolve)
    monkeypatch.setattr(
        client, "build_custom_recipe_payload", fake.build_custom_recipe_payload
    )
    registry = FakeRegistry()
    registry.client = client
    mcp = FakeMCP()
    setup_tools(mcp, registry)
    rows = [{"food_guid": f"food-{i}", "amount": i + 1} for i in range(7)]
    args = (
        {"account": "personal", "items": rows, "date": "2026-01-01"}
        if tool == "record_food_batch"
        else {
            "account": "personal",
            "title": "Test",
            "ingredients": rows,
        }
    )
    result = await mcp.tools[tool](**args)
    assert result["success_count"] == 7
    assert peak == 4
    assert active == 0
    output = result["results"] if tool == "record_food_batch" else result["ingredients"]
    assert [item["index"] for item in output] == list(range(7))
    if tool == "record_food_batch":
        assert [item["preview"]["food_guid"] for item in output] == [
            row["food_guid"] for row in rows
        ]
    assert auth.calls["login"] == auth.calls["data"] == 0


async def test_batch_writes_remain_sequential_and_stop_on_error(auth, monkeypatch):
    client = auth.client
    fake = FakeClient()
    monkeypatch.setattr(client, "_resolve_food", fake.resolve_food)
    monkeypatch.setattr(client, "get_duplicate_entries", AsyncMock(return_value=[]))
    written = []

    async def write(resolved, **kwargs):
        written.append(resolved["food_guid"])
        if len(written) == 2:
            raise KalorickeTabulkyError("fixture write failure")
        return {"message": "ok"}

    monkeypatch.setattr(client, "record_resolved_food", write)
    registry = FakeRegistry()
    registry.client = client
    mcp = FakeMCP()
    setup_tools(mcp, registry)
    result = await mcp.tools["record_food_batch"](
        account="personal",
        date="2026-01-01",
        commit=True,
        continue_on_error=False,
        items=[{"food_guid": f"food-{i}", "amount": 1} for i in range(4)],
    )
    assert written == ["food-0", "food-1"]
    assert result["failure_count"] == 1


async def test_resolution_error_prevents_all_writes(auth, monkeypatch):
    fake = FakeClient()

    async def resolve(**kwargs):
        if kwargs["food_guid"] == "bad":
            raise KalorickeTabulkyError("fixture resolution failure")
        return await fake.resolve_food(**kwargs)

    monkeypatch.setattr(auth.client, "_resolve_food", resolve)
    write = AsyncMock()
    monkeypatch.setattr(auth.client, "record_resolved_food", write)
    registry = FakeRegistry()
    registry.client = auth.client
    mcp = FakeMCP()
    setup_tools(mcp, registry)
    result = await mcp.tools["record_food_batch"](
        account="personal",
        date="2026-01-01",
        commit=True,
        continue_on_error=False,
        items=[{"food_guid": guid, "amount": 1} for guid in ("good", "bad", "later")],
    )
    assert result["failure_count"] == 1
    assert [row["index"] for row in result["results"]] == [0, 1]
    write.assert_not_called()


async def test_ranges_and_resolutions_share_one_semaphore(auth, monkeypatch):
    active = peak = 0
    gate = asyncio.Event()
    fake = FakeClient()

    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 4:
            gate.set()
        try:
            await asyncio.wait_for(gate.wait(), 2)
            await asyncio.sleep(0)
        finally:
            active -= 1

    async def resolve(**kwargs):
        await work()
        return await fake.resolve_food(**kwargs)

    async def summary(day, **kwargs):
        await work()
        return SummaryData([], {}, {"detail": {"foodstuff": []}})

    monkeypatch.setattr(auth.client, "_resolve_food", resolve)
    monkeypatch.setattr(auth.client, "get_summary", summary)
    await asyncio.gather(
        auth.client.get_diary_range(date(2026, 1, 1), date(2026, 1, 7)),
        *(
            auth.client.resolve_food(
                food_guid=f"food-{i}", target_date=date(2026, 1, 1)
            )
            for i in range(7)
        ),
    )
    assert peak == 4
    assert active == 0
