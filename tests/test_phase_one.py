"""Phase 1 regressions. All HTTP requests go to a local fake KT server."""

import asyncio
import json
from pathlib import Path
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
from kaloricke_tabulky_mcp.diary import duplicate_entries, parse_unit_text
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
    exact = candidate("exact", "Rýže")
    assert _select_food([exact, exact], "ryze", allow_fuzzy=False) == exact


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
                    "unitOptions": [
                        {"id": "g", "title": "1 g", "multiplier": 1},
                        {"id": "ml", "title": "1 ml", "multiplier": 1},
                        {"id": "piece", "title": "kus (42 g)", "multiplier": 42},
                    ],
                    "selectedUnitGuid": "portion",
                    "selectedUnitMultiplier": 1,
                    "portionsMax": 1,
                    "units": [{"id": "portion", "title": "porce", "multiplier": -2}],
                    "foodstuff": [
                        {
                            "count": 250,
                            "selectedUnitGuid": "g",
                            "units": [{"id": "g", "multiplier": 1}],
                        }
                    ],
                },
            }
        )

    async def diary(request):
        # Mirrors the live user/diary/{date}/get shape: rows grouped in times[],
        # portions only as display text, sibling activities and totals.
        state.calls["diary"] += 1
        if state.detail_override is not None:
            return web.json_response(state.detail_override)
        rows = state.rows.get(request.match_info["date"], [])
        return web.json_response(
            {
                "code": 0,
                "data": {
                    "times": [
                        {
                            "id": meal,
                            "title": f"meal {meal}",
                            "foodstuff": [row for at, row in rows if at == meal],
                            "notes": [],
                        }
                        for meal in "123456"
                    ],
                    "activities": [
                        {"id": "activity", "type": "activity", "unit": "44 min"}
                    ],
                    "notes": [],
                    "energyTotal": 0,
                },
            }
        )

    def amount(value):
        return f"{float(value):g}".replace(".", ",")

    async def record(request):
        assert request.headers["Accept"] == "application/json, text/plain, */*"
        state.calls["post"] += 1
        body = await request.json()
        if request.path == "/record-recipe":
            total = sum(item["count"] for item in body["foodstuff"])
            unit = f"{amount(total)} x 1 g"
        else:
            title = next(
                o["title"] for o in body["unitOptions"] if o["id"] == body["unitGuid"]
            )
            count = float(body["multiplier"])
            unit = title if count == 1 else f"{amount(count)} x {title}"
        row = {
            "id": f"entry-{state.calls['post']}",
            "type": "foodstuff",
            "guidType": body["foodstuffGuid"],
            "title": body["title"],
            "time": body.get("time") or "12:00",
            "unit": unit,
        }
        state.rows.setdefault(body["date"], []).append((body["diaryTimeGuid"], row))
        return web.json_response(
            {"code": 0, "data": {"guid": row["id"]}, "message": "ok"}
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
        "default_meal_type": "lunch",
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


ROW = {"id": "entry", "guidType": "rice", "unit": "1 g"}


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"foodstuff": []},
        {"times": {}},
        {"times": [{"id": "7", "foodstuff": []}]},
        {"times": [{"id": "3", "foodstuff": {}}]},
        {"times": [{"id": "3", "foodstuff": [{**ROW, "id": None}]}]},
        {"times": [{"id": "3", "foodstuff": [{**ROW, "guidType": ""}]}]},
        {"times": [{"id": "3", "foodstuff": [{**ROW, "unit": ""}]}]},
        {"times": [{"id": "3", "foodstuff": [{**ROW, "unit": "0 x kus (42 g)"}]}]},
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
        default_meal_type="lunch",
        commit=True,
        items=[{"food_guid": "rice", "amount": 100}],
    )
    assert result["status"] == "error"
    assert kt.calls["post"] == 0


@pytest.mark.parametrize("commit", [False, True])
async def test_batch_with_ambiguity_writes_nothing(kt, commit):
    kt.searches = [{"id": "a", "title": "Rýže A"}, {"id": "b", "title": "Rýže B"}]
    result = await kt.tools["record_food_batch"](
        account="personal",
        date="2026-01-01",
        default_meal_type="lunch",
        commit=commit,
        items=[{"food_guid": "chicken", "amount": 100}, {"query": "ryze", "amount": 1}],
    )
    assert result["failure_count"] == 1
    assert result["results"][1]["ambiguous"] is True
    # Same status contract as the single-item tools.
    assert result["results"][1]["status"] == ("error" if commit else "ambiguous")
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
        default_meal_type="lunch",
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
        food_guid="rice", target_date=date(2026, 1, 1), amount=100, meal_type="lunch"
    )
    first = await kt.client.record_resolved_food(resolved)
    assert first["entry_guid"] == "entry-1"
    resolved["unit_guid"] = resolved["payload"]["unitGuid"] = "ml"
    second = await kt.client.record_resolved_food(resolved)
    assert second["entry_guid"] == "entry-2"
    assert kt.calls["post"] == 2


async def test_same_quantity_in_another_unit_is_duplicate(kt):
    args = {
        "account": "personal",
        "date": "2026-01-01",
        "meal_type": "breakfast",
        "food_guid": "roll",
        "commit": True,
    }
    first = await kt.tools["record_food"](**args, amount=63, unit_guid="g")
    second = await kt.tools["record_food"](**args, amount=1.5, unit_guid="piece")
    assert first["status"] == "written"
    assert second["status"] == "skipped_duplicate"
    assert second["existing_record"]["unit"] == "63 x 1 g"
    assert kt.calls["post"] == 1


async def test_recipe_serving_compares_total_weight(kt):
    args = {
        "account": "personal",
        "date": "2026-01-01",
        "meal_type": "dinner",
        "recipe_guid": "soup",
        "amount": 1,
        "commit": True,
    }
    assert (await kt.tools["record_recipe_serving"](**args))["status"] == "written"
    assert kt.rows["01.01.2026"][0][1]["unit"] == "250 x 1 g"
    second = await kt.tools["record_recipe_serving"](**args)
    assert second["status"] == "skipped_duplicate"
    assert second["existing_record"]["portion"]["quantity"] == 250


@pytest.mark.parametrize(
    "tool,reference",
    [
        ("record_food", {"food_guid": "rice"}),
        ("record_recipe_serving", {"recipe_guid": "recipe"}),
    ],
)
async def test_other_day_requires_explicit_meal_or_time(kt, freeze, tool, reference):
    freeze("2026-01-02T10:00:00+00:00")
    result = await kt.tools[tool](account="personal", date="2026-01-01", **reference)
    assert result["status"] == "error"
    assert "meal_type or time" in result["error"]
    assert kt.calls["form"] == kt.calls["search"] == 0
    result = await kt.tools[tool](
        account="personal", date="2026-01-01", time="19:00", **reference
    )
    assert (result["meal_type"], result["meal_type_source"]) == ("5", "time")


@pytest.mark.parametrize("commit", [False, True])
async def test_single_non_exact_result_is_not_auto_selected(kt, commit):
    kt.searches = [{"id": "roll", "title": "Rohlík tukový", "clazz": "foodstuff"}]
    result = await kt.tools["record_food"](
        account="personal", query="rohlik", amount=1, commit=commit
    )
    assert result["ambiguous"] is True
    assert result["status"] == ("error" if commit else "ambiguous")
    assert "exact" in result["error"]
    assert kt.calls["form"] == kt.calls["post"] == 0
    exact = await kt.tools["record_food"](account="personal", query="ROHLIK tukovy")
    assert exact["food_guid"] == "roll"


# Anonymized excerpt of a live user/diary/{date}/get payload (2026-11).
LIVE_DIARY = json.loads(
    (Path(__file__).parent / "fixtures" / "diary_live_anonymized.json").read_text()
)["data"]


def test_live_diary_payload_is_normalized():
    entries = duplicate_entries({"data": LIVE_DIARY})
    summary = [
        (
            e["meal_type"],
            e["food_guid"][:4],
            e["entry_guid"][-1],
            e["portion"]["quantity"],
            e["portion"]["dimension"],
        )
        for e in entries
    ]
    assert summary == [
        ("1", "r000", "1", 306.23, "g"),
        ("1", "f000", "2", 220, "ml"),
        ("1", "f000", "3", 63, "g"),
        ("5", "f000", "4", 50, "g"),
        ("5", "f000", "5", 1500, "ml"),
    ]


def test_empty_live_day_has_no_entries():
    empty = {
        "times": [{"id": str(i), "foodstuff": [], "notes": []} for i in range(1, 7)],
        "activities": [],
        "notes": [],
    }
    assert duplicate_entries({"data": empty}) == []


@pytest.mark.parametrize(
    "text,label,count,quantity,dimension",
    [
        ("kus (88 g)", "kus (88 g)", 1, 88, "g"),
        ("3 x plátek (17 g)", "plátek (17 g)", 3, 51, "g"),
        ("170 x 1 g", "1 g", 170, 170, "g"),
        ("1 208,5 x 1 g", "1 g", 1208.5, 1208.5, "g"),
        ("balení (0,5 l)", "balení (0.5 l)", 1, 500, "ml"),
        ("2 x lžíce", "lžíce", 2, None, None),
    ],
)
def test_parse_unit_text(text, label, count, quantity, dimension):
    portion = parse_unit_text(text)
    assert (portion["label"], portion["count"], portion["dimension"]) == (
        label,
        count,
        dimension,
    )
    assert portion["quantity"] == (pytest.approx(quantity) if quantity else None)
