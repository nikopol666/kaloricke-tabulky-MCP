"""Phase 1 regressions. All HTTP requests go to a local fake KT server."""

import asyncio
from collections import Counter
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
import pytest

from kaloricke_tabulky_mcp import clock, models
from kaloricke_tabulky_mcp import client as client_module
from kaloricke_tabulky_mcp.account_registry import AccountRegistry
from kaloricke_tabulky_mcp.client import (
    AmbiguousFoodError,
    KalorickeTabulkyClient,
    KalorickeTabulkyError,
    _select_food,
    parse_public_date,
)
from kaloricke_tabulky_mcp.config import ConfigError, load_settings
from kaloricke_tabulky_mcp.diary import duplicate_entries
from kaloricke_tabulky_mcp.tools import setup_tools


class ToolCollector:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(func):
            self.tools[func.__name__] = func
            return func

        return register


@pytest.fixture
def freeze(monkeypatch):
    def at(iso):
        instant = datetime.fromisoformat(iso)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                assert tz is not None, "The application clock must be timezone-aware"
                return instant.astimezone(tz)

        monkeypatch.setattr(clock, "datetime", FrozenDatetime)

    return at


@pytest.mark.parametrize(
    "timezone,instant,expected",
    [
        ("Europe/Prague", "2026-01-01T23:30:00+00:00", "2026-01-02"),
        ("Europe/Prague", "2026-07-01T22:30:00+00:00", "2026-07-02"),
        ("America/New_York", "2026-01-02T00:30:00+00:00", "2026-01-01"),
    ],
)
def test_local_dates_use_timezone(monkeypatch, freeze, timezone, instant, expected):
    monkeypatch.setenv("KT_TIMEZONE", timezone)
    freeze(instant)
    assert clock.local_today().isoformat() == expected
    assert parse_public_date(None).isoformat() == expected
    assert models.parse_date(None).isoformat() == expected
    for model, args in [
        (models.RecordFoodRequest, {"account": "personal", "food_guid": "rice"}),
        (
            models.RecordFoodBatchRequest,
            {"account": "personal", "items": [{"food_guid": "rice", "amount": 1}]},
        ),
        (
            models.RecordRecipeServingRequest,
            {"account": "personal", "recipe_guid": "recipe"},
        ),
        (models.DiarySummaryRequest, {}),
        (models.RecordWeightRequest, {"account": "personal", "weight_kg": 70}),
    ]:
        assert model(**args).date.isoformat() == expected
        assert model(**args, date=None).date.isoformat() == expected
    assert parse_public_date("2026-01-01") == date(2026, 1, 1)


@pytest.mark.parametrize("name", ["not/a/timezone", "", "../Europe/Prague"])
def test_invalid_timezone_is_configuration_error(name):
    with pytest.raises(ConfigError, match="KT_TIMEZONE"):
        load_settings({"KT_TIMEZONE": name})


def test_timezone_is_loaded_from_env_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KT_TIMEZONE", raising=False)
    monkeypatch.setenv("KT_ACCOUNTS", "personal")
    monkeypatch.setenv("KT_ACCOUNT_PERSONAL_EMAIL", "fixture@example.com")
    monkeypatch.setenv("KT_ACCOUNT_PERSONAL_PASSWORD", "fixture")
    (tmp_path / ".env").write_text("KT_TIMEZONE=America/New_York\n")
    assert load_settings().timezone == "America/New_York"


def candidate(guid, title, favorite=False):
    return {
        "food_guid": guid,
        "title": title,
        "favorite": favorite,
        "class": "foodstuff",
        "brand_name": "Test",
        "energy": 123,
        "unit": "g",
    }


def test_exact_normalized_match_beats_favorite():
    favorite = candidate("fuzzy", "Rýže vařená", True)
    exact = candidate("exact", "  RÝŽE  ")
    assert _select_food([favorite, exact], "ryze", allow_fuzzy=False) == exact


def test_favorite_then_prefix_ranking_requires_explicit_fuzzy_opt_in():
    favorite = candidate("favorite", "Vařená rýže", True)
    prefix = candidate("prefix", "Rýže vařená")
    other = candidate("other", "Divoká rýže")
    with pytest.raises(AmbiguousFoodError) as exc:
        _select_food([other, prefix, favorite], "ryze", allow_fuzzy=False)
    assert [c["food_guid"] for c in exc.value.candidates] == [
        "favorite",
        "prefix",
        "other",
    ]
    assert _select_food([other, prefix, favorite], "ryze", allow_fuzzy=True) == favorite


def test_tied_exact_names_are_ambiguous_but_favorite_breaks_tie():
    a, b = candidate("a", "Rýže"), candidate("b", "Rýže")
    with pytest.raises(AmbiguousFoodError):
        _select_food([a, b], "ryze", allow_fuzzy=False)
    b["favorite"] = True
    assert _select_food([a, b], "ryze", allow_fuzzy=False) == b


def test_candidate_limit_and_distinct_ids():
    results = [candidate(str(i), f"Rýže {i}") for i in range(8)]
    with pytest.raises(AmbiguousFoodError) as exc:
        _select_food(results, "ryze", allow_fuzzy=False)
    assert len(exc.value.candidates) == 5
    assert set(exc.value.candidates[0]) == {
        "food_guid",
        "title",
        "brand_name",
        "energy",
        "unit",
    }
    assert (
        _select_food([results[0], results[0]], "ryze", allow_fuzzy=False) == results[0]
    )


@pytest.fixture
async def kt(aiohttp_server, monkeypatch):
    state = SimpleNamespace(rows={}, calls=Counter(), searches=[], detail_override=None)

    async def search(request):
        state.calls["search"] += 1
        return web.json_response(state.searches)

    async def form(request):
        state.calls["form"] += 1
        guid = request.match_info["guid"]
        return web.json_response(
            {
                "code": 0,
                "data": {
                    "foodstuffGuid": guid,
                    "title": guid,
                    "unitGuid": "g",
                    "multiplier": 1,
                    "unitOptions": [{"id": "g", "title": "1 g", "multiplier": 1}],
                    "selectedUnitGuid": "portion",
                    "selectedUnitMultiplier": 1,
                    "units": [{"id": "portion", "title": "porce", "multiplier": -2}],
                },
            }
        )

    async def diary(request):
        state.calls["diary"] += 1
        if state.detail_override is not None:
            return web.json_response(state.detail_override)
        return web.json_response(
            {
                "code": 0,
                "data": {"foodstuff": state.rows.get(request.match_info["date"], [])},
            }
        )

    async def record(request):
        assert request.headers["Accept"] == "application/json, text/plain, */*"
        state.calls["post"] += 1
        body = await request.json()
        recipe = request.path == "/record-recipe"
        row = {
            "guid": f"entry-{state.calls['post']}",
            "foodstuffGuid": body["foodstuffGuid"],
            "diaryTimeGuid": body["diaryTimeGuid"],
            "unitGuid": body["selectedUnitGuid"] if recipe else body["unitGuid"],
            "multiplier": (
                body["selectedUnitMultiplier"] if recipe else body["multiplier"]
            ),
        }
        state.rows.setdefault(body["date"], []).append(row)
        return web.json_response(
            {"code": 0, "data": {"guid": row["guid"]}, "message": "ok"}
        )

    app = web.Application()
    app.router.add_get("/search", search)
    app.router.add_get("/form/{guid}/{date}", form)
    app.router.add_get("/recipe/{guid}", form)
    app.router.add_get("/diary/{date}", diary)
    app.router.add_post("/record-food", record)
    app.router.add_post("/record-recipe", record)
    server = await aiohttp_server(app)
    base = str(server.make_url("/")).rstrip("/")
    for name, path in {
        "SEARCH_FOOD_URL": "/search?{query}",
        "FOOD_FORM_URL": "/form/{guid}/{date}",
        "RECIPE_FORM_URL": "/recipe/{guid}",
        "DIARY_DETAIL_URL": "/diary/{date}",
        "RECORD_FOOD_URL": "/record-food",
        "RECORD_RECIPE_URL": "/record-recipe",
    }.items():
        monkeypatch.setattr(client_module, name, base + path)
    settings = load_settings(
        {
            "KT_ACCOUNTS": "personal",
            "KT_ACCOUNT_PERSONAL_EMAIL": "test@example.com",
            "KT_ACCOUNT_PERSONAL_PASSWORD": "fixture",
            "KT_TIMEZONE": "Europe/Prague",
        }
    )
    registry = AccountRegistry(settings)
    client = KalorickeTabulkyClient(
        settings.accounts["personal"], timezone=settings.timezone
    )
    client._cookies = "fixture=local-only"
    registry._clients["personal"] = client
    mcp = ToolCollector()
    setup_tools(mcp, registry)
    state.client, state.tools, state.registry = client, mcp.tools, registry
    try:
        yield state
    finally:
        await registry.close()


@pytest.mark.parametrize(
    "tool,reference",
    [
        ("record_food", {"food_guid": "rice"}),
        ("record_recipe_serving", {"recipe_guid": "recipe"}),
    ],
)
@pytest.mark.parametrize("hour,meal", [(13, "3"), (19, "5")])
async def test_missing_meal_uses_local_now(kt, freeze, tool, reference, hour, meal):
    freeze(f"2026-01-01T{hour - 1:02d}:00:00+00:00")
    result = await kt.tools[tool](account="personal", **reference)
    assert result["meal_type"] == meal
    assert result["meal_type_source"] == "now"
    assert kt.calls["diary"] == kt.calls["post"] == 0


@pytest.mark.parametrize(
    "kwargs,meal,source",
    [
        ({"meal_type": "breakfast", "time": "19:00"}, "1", "explicit"),
        ({"time": "13:00"}, "3", "time"),
    ],
)
async def test_meal_precedence(kt, kwargs, meal, source):
    result = await kt.tools["record_food"](
        account="personal", food_guid="rice", **kwargs
    )
    assert (result["meal_type"], result["meal_type_source"]) == (meal, source)


async def test_tools_use_validated_timezone_for_default_dates(kt, freeze, monkeypatch):
    # The settings timezone must win even if the ambient environment differs.
    monkeypatch.setenv("KT_TIMEZONE", "UTC")
    freeze("2026-01-01T23:30:00+00:00")
    result = await kt.tools["record_food"](account="personal", food_guid="rice")
    assert result["date"] == "02.01.2026"
    for tool in ("prepare_recipe_import", "create_custom_recipe"):
        spy = AsyncMock(wraps=kt.client.resolve_food)
        monkeypatch.setattr(kt.client, "resolve_food", spy)
        await kt.tools[tool](
            account="personal",
            title="Test",
            ingredients=[{"food_guid": "rice", "amount": 1}],
        )
        assert spy.call_args.kwargs["target_date"] == date(2026, 1, 2)


@pytest.mark.parametrize("commit", [False, True])
async def test_ambiguous_food_never_fetches_form_or_writes(kt, commit):
    kt.searches = [
        {"id": str(i), "title": f"Rýže {i}", "clazz": "foodstuff"} for i in range(8)
    ]
    result = await kt.tools["record_food"](
        account="personal", query="rýže", commit=commit
    )
    assert result["ambiguous"] is True
    assert len(result["candidates"]) == 5
    assert result["status"] == ("error" if commit else "ambiguous")
    assert kt.calls["form"] == kt.calls["diary"] == kt.calls["post"] == 0


async def test_fuzzy_override_and_explicit_guid(kt):
    kt.searches = [
        {"id": "other", "title": "Rýže jiná", "clazz": "foodstuff"},
        {
            "id": "favorite",
            "title": "Vařená rýže",
            "clazz": "foodstuff",
            "favorite": True,
        },
    ]
    result = await kt.tools["record_food"](
        account="personal", query="ryze", amount=100, commit=True, allow_fuzzy=True
    )
    assert result["status"] == "written"
    assert result["food_guid"] == "favorite"
    kt.calls.clear()
    result = await kt.tools["record_food"](
        account="personal", query="ignored", food_guid="chosen"
    )
    assert result["food_guid"] == "chosen"
    assert kt.calls["search"] == 0


@pytest.mark.parametrize(
    "tool,reference",
    [
        ("record_food", {"food_guid": "rice"}),
        ("record_recipe_serving", {"recipe_guid": "recipe"}),
    ],
)
async def test_repeated_single_write_is_skipped(kt, tool, reference):
    args = {
        "account": "personal",
        "date": "2026-01-01",
        "meal_type": "lunch",
        "amount": 1,
        "commit": True,
        **reference,
    }
    first = await kt.tools[tool](**args)
    second = await kt.tools[tool](**args)
    assert first["status"] == "written"
    assert second["status"] == "skipped_duplicate"
    assert second["existing_record"]["entry_guid"] == "entry-1"
    assert kt.calls["post"] == 1
    third = await kt.tools[tool](**args, allow_duplicates=True)
    assert third["status"] == "written"
    assert kt.calls["diary"] == 2
    assert kt.calls["post"] == 2


async def test_repeated_batch_reads_diary_once_per_attempt(kt):
    args = {
        "account": "personal",
        "date": "2026-01-01",
        "default_meal_type": "lunch",
        "commit": True,
        "items": [
            {"food_guid": "rice", "amount": 100},
            {"food_guid": "chicken", "amount": 150},
        ],
    }
    first = await kt.tools["record_food_batch"](**args)
    second = await kt.tools["record_food_batch"](**args)
    assert [r["status"] for r in first["results"]] == ["written", "written"]
    assert [r["status"] for r in second["results"]] == [
        "skipped_duplicate",
        "skipped_duplicate",
    ]
    assert kt.calls["post"] == kt.calls["diary"] == 2
    assert [r["existing_record"]["entry_guid"] for r in second["results"]] == [
        "entry-1",
        "entry-2",
    ]


async def test_duplicates_within_batch_and_opt_out(kt):
    args = {
        "account": "personal",
        "date": "2026-01-01",
        "commit": True,
        "items": [{"food_guid": "rice", "amount": 100}] * 2,
    }
    result = await kt.tools["record_food_batch"](**args)
    assert [r["status"] for r in result["results"]] == ["written", "skipped_duplicate"]
    assert kt.calls["post"] == kt.calls["diary"] == 1
    result = await kt.tools["record_food_batch"](**args, allow_duplicates=True)
    assert [r["status"] for r in result["results"]] == ["written", "written"]
    assert kt.calls["diary"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"meal_type": "dinner"},
        {"amount": 101},
        {"food_guid": "chicken"},
        {"date": "2026-01-02"},
    ],
)
async def test_different_entries_are_not_duplicates(kt, change):
    args = {
        "account": "personal",
        "date": "2026-01-01",
        "meal_type": "lunch",
        "amount": 100,
        "food_guid": "rice",
        "commit": True,
    }
    await kt.tools["record_food"](**args)
    result = await kt.tools["record_food"](**{**args, **change})
    assert result["status"] == "written"
    assert kt.calls["post"] == 2


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"unrecognized": [1]},
        {"foodstuff": [{"guid": "unknown"}]},
        {
            "foodstuff": [
                {"foodstuffGuid": "rice", "guid": "entry", "diaryTimeGuid": "3"}
            ]
        },
    ],
)
async def test_unknown_diary_shape_blocks_writes(kt, data):
    kt.detail_override = {"code": 0, "data": data}
    result = await kt.tools["record_food"](
        account="personal", food_guid="rice", amount=1, commit=True
    )
    assert result["status"] == "error"
    assert "duplicate" in result["error"]
    assert kt.calls["post"] == 0


async def test_diary_read_failure_blocks_entire_batch(kt):
    kt.detail_override = {"code": 99, "message": "fixture failure"}
    result = await kt.tools["record_food_batch"](
        account="personal",
        date="2026-01-01",
        commit=True,
        items=[{"food_guid": "rice", "amount": 100}],
    )
    assert result["status"] == "error"
    assert kt.calls["post"] == 0


async def test_batch_with_ambiguity_writes_nothing(kt):
    kt.searches = [{"id": "a", "title": "Rýže A"}, {"id": "b", "title": "Rýže B"}]
    result = await kt.tools["record_food_batch"](
        account="personal",
        date="2026-01-01",
        commit=True,
        items=[{"food_guid": "chicken", "amount": 100}, {"query": "ryze", "amount": 1}],
    )
    assert result["failure_count"] == 1
    assert result["results"][1]["ambiguous"] is True
    assert kt.calls["post"] == kt.calls["diary"] == 0


async def test_concurrent_writes_on_one_client_do_not_duplicate(kt):
    args = {"account": "personal", "food_guid": "rice", "amount": 100, "commit": True}
    results = await asyncio.gather(*(kt.tools["record_food"](**args) for _ in range(3)))
    assert sorted(r["status"] for r in results) == [
        "skipped_duplicate",
        "skipped_duplicate",
        "written",
    ]
    assert kt.calls["post"] == 1


async def test_batch_reference_when_write_response_omits_entry_id(kt, monkeypatch):
    original = kt.client._request_with_reauth

    async def request(method, url, **kwargs):
        result = await original(method, url, **kwargs)
        if method == "POST":
            return {"code": 0, "message": "ok"}
        return result

    monkeypatch.setattr(kt.client, "_request_with_reauth", request)
    result = await kt.tools["record_food_batch"](
        account="personal",
        date="2026-01-01",
        commit=True,
        items=[{"food_guid": "rice", "amount": 100}] * 2,
    )
    reference = result["results"][1]["existing_record"]
    assert reference["entry_guid"] is None
    assert reference["written_in_request"] is True
    assert reference["write_index"] == 0
    assert result["written_count"] == result["skipped_duplicate_count"] == 1
    assert kt.calls["post"] == 1


async def test_retry_after_lost_write_response_does_not_duplicate(kt, monkeypatch):
    original = kt.client._request_with_reauth
    lost = False

    async def request(method, url, **kwargs):
        nonlocal lost
        result = await original(method, url, **kwargs)
        if method == "POST" and not lost:
            lost = True
            raise TimeoutError("Simulated response lost after server accepted write")
        return result

    monkeypatch.setattr(kt.client, "_request_with_reauth", request)
    args = {"account": "personal", "food_guid": "rice", "amount": 100, "commit": True}
    with pytest.raises(TimeoutError):
        await kt.tools["record_food"](**args)
    result = await kt.tools["record_food"](**args)
    assert result["status"] == "skipped_duplicate"
    assert kt.calls["post"] == 1


async def test_summary_default_date_uses_client_timezone(kt, freeze, monkeypatch):
    freeze("2026-01-01T23:30:00+00:00")
    monkeypatch.setenv("KT_TIMEZONE", "UTC")
    request = AsyncMock(return_value={"code": 0, "data": {}})
    monkeypatch.setattr(kt.client, "_request_with_reauth", request)
    await kt.client.get_summary()
    assert request.await_count == 3
    assert all("02.01.2026" in call.args[1] for call in request.await_args_list)


async def test_recipe_lookup_also_blocks_ambiguity(kt, monkeypatch):
    recipes = AsyncMock(
        return_value={
            "recipes": [
                {"recipe_guid": "a", "title": "Polévka A"},
                {"recipe_guid": "b", "title": "Polévka B"},
            ]
        }
    )
    monkeypatch.setattr(kt.client, "list_custom_recipes", recipes)
    result = await kt.tools["record_recipe_serving"](
        account="personal", query="polevka", commit=True
    )
    assert result["ambiguous"] is True
    assert kt.calls["post"] == kt.calls["form"] == 0
    result = await kt.tools["record_recipe_serving"](
        account="personal", query="polevka", allow_fuzzy=True
    )
    assert result["food_guid"] == "a"


async def test_different_units_are_not_duplicates(kt, monkeypatch):
    resolved = await kt.client.resolve_food(
        food_guid="rice", target_date=date(2026, 1, 1), amount=100
    )
    first = await kt.client.record_resolved_food(resolved)
    assert first["entry_guid"] == "entry-1"
    resolved["unit_guid"] = resolved["payload"]["unitGuid"] = "ml"
    second = await kt.client.record_resolved_food(resolved)
    assert second["entry_guid"] == "entry-2"
    assert kt.calls["post"] == 2


def test_grouped_diary_inherits_meal_and_ignores_recipe_ingredients():
    row = {
        "foodstuffGuid": "recipe",
        "guid": "entry",
        "selectedUnitGuid": "portion",
        "selectedUnitMultiplier": "1,5",
        "foodstuff": [{"guid": "ingredient"}],
    }
    entries = duplicate_entries(
        {"data": {"meals": [{"diaryTimeGuid": "3", "foodstuff": [row]}]}}
    )
    assert entries == [
        {
            "entry_guid": "entry",
            "food_guid": "recipe",
            "meal_type": "3",
            "unit_guid": "portion",
            "multiplier": 1.5,
        }
    ]
