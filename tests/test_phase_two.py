import asyncio
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from kaloricke_tabulky_mcp.client import (
    AccountCredentials,
    KalorickeTabulkyClient,
    KalorickeTabulkyError,
    SummaryData,
    SummaryMetric,
    WeightRecord,
)
from kaloricke_tabulky_mcp.config import load_settings
from kaloricke_tabulky_mcp.diary import compact_diary
from kaloricke_tabulky_mcp.models import DiaryRangeRequest
from kaloricke_tabulky_mcp.trends import range_day, range_summary
from kaloricke_tabulky_mcp.tools import setup_tools


@pytest.fixture
async def client():
    instance = KalorickeTabulkyClient(
        AccountCredentials("personal", "fixture@example.com", "fixture")
    )
    try:
        yield instance
    finally:
        await instance.close()


def summary(energy=None, logged=False):
    metrics = (
        {}
        if energy is None
        else {
            "energy": SummaryMetric("energy", "Energy", energy, "kcal", goal=2000),
            "protein": SummaryMetric("protein", "Protein", energy / 10, "g"),
        }
    )
    return SummaryData(
        [], metrics, {"detail": {"foodstuff": [{"title": "fixture"}] if logged else []}}
    )


async def test_seven_day_range_averages_exclude_two_empty_days(client, monkeypatch):
    start = date(2026, 1, 1)
    values = [1000, None, 1200, 1400, None, 1600, 1800]

    async def get(day, **kwargs):
        assert kwargs == {"require_detail": True}
        value = values[(day - start).days]
        return summary(value, logged=value is not None)

    monkeypatch.setattr(client, "get_summary", get)
    result = await client.get_diary_range(start, start + timedelta(days=6))
    assert [row["date"] for row in result["days"]] == [
        (start + timedelta(days=i)).isoformat() for i in range(7)
    ]
    assert [row["logged"] for row in result["days"]] == [
        True,
        False,
        True,
        True,
        False,
        True,
        True,
    ]
    stats = result["summary"]
    assert stats["days_with_record"] == 5
    assert stats["empty_days"] == 2
    assert stats["averages"]["energy_kcal"] == 1400
    assert stats["averages"]["protein_g"] == 140
    assert stats["average_sample_counts"]["energy_kcal"] == 5
    assert stats["averages"]["fat_g"] is None


async def test_range_has_real_bounded_parallelism(client, monkeypatch):
    gate = asyncio.Event()
    active = maximum = 0

    async def get(day, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 4:
            gate.set()
        try:
            await asyncio.wait_for(gate.wait(), 2)
            return summary(1000, True)
        finally:
            active -= 1

    monkeypatch.setattr(client, "get_summary", get)
    await client.get_diary_range(date(2026, 1, 1), date(2026, 1, 7))
    assert maximum == 4


async def test_range_failure_cancels_pending_work(client, monkeypatch):
    active = 0
    ready = asyncio.Event()

    async def get(day, **kwargs):
        nonlocal active
        active += 1
        if active == 4:
            ready.set()
        try:
            await ready.wait()
            if day.day == 1:
                raise KalorickeTabulkyError("fixture read failure")
            await asyncio.Event().wait()
        finally:
            active -= 1

    monkeypatch.setattr(client, "get_summary", get)
    with pytest.raises(KalorickeTabulkyError, match="fixture read failure"):
        await asyncio.wait_for(
            client.get_diary_range(date(2026, 1, 1), date(2026, 1, 7)), 2
        )
    assert active == 0


@pytest.mark.parametrize("end", ["2025-12-31", "2026-04-03"])
async def test_range_limit_validates_before_fetch(client, monkeypatch, end):
    get = AsyncMock()
    monkeypatch.setattr(client, "get_summary", get)
    with pytest.raises(ValidationError):
        await client.get_diary_range(date(2026, 1, 1), date.fromisoformat(end))
    get.assert_not_called()


def test_range_inclusive_limits_and_missing_dates():
    assert DiaryRangeRequest(
        account="personal", date_from="2026-01-01", date_to="2026-04-02"
    )
    with pytest.raises(ValidationError):
        DiaryRangeRequest(account="personal", date_from=None, date_to="2026-01-01")


def test_range_unit_conversion_goal_weight_and_missing_values():
    data = SummaryData(
        [WeightRecord("2026-01-02", 80)],
        {
            "energy": SummaryMetric("energy", "Energy", 4184, "kJ", goal=8368),
            "salt": SummaryMetric("salt", "Salt", 500, "mg"),
            "cilova_hmotnost": SummaryMetric(
                "cilova_hmotnost", "Target weight", 60, "kg"
            ),
        },
        {"detail": {"foodstuff": []}},
    )
    row = range_day(date(2026, 1, 1), data)
    assert row["energy_kcal"] == pytest.approx(1000)
    assert row["energy_target_kcal"] == pytest.approx(2000)
    assert row["salt_g"] == 0.5
    assert row["weight_kg"] is None
    assert range_day(date(2026, 1, 2), data)["weight_kg"] == 80
    assert row["fiber_g"] is None


def test_range_weight_uses_live_two_digit_year_labels():
    data = SummaryData(
        [WeightRecord("28.10.26", 74.4), WeightRecord("05.11.26", 74.9)], {}, {"detail": {}}
    )
    assert range_day(date(2026, 11, 5), data)["weight_kg"] == 74.9
    assert range_day(date(2026, 11, 6), data)["weight_kg"] is None


def test_zero_energy_entry_is_logged_but_goals_and_weight_are_not():
    row = range_day(date(2026, 1, 1), summary(0, logged=True))
    assert row["logged"] is True
    data = summary()
    data.metrics.update(
        energy_target=SummaryMetric("energy_target", "Goal", 2000, "kcal"),
        weight=SummaryMetric("weight", "Weight", 80, "kg"),
    )
    empty = range_day(date(2026, 1, 2), data)
    assert empty["logged"] is False
    assert range_summary([empty])["averages"]["weight_kg"] is None


@pytest.mark.parametrize("value", ["0", "33", "invalid"])
def test_concurrency_config_is_validated(value):
    with pytest.raises(ValidationError):
        load_settings({"KT_MAX_CONCURRENCY": value})


@pytest.fixture
def diary():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "diary_live_anonymized.json").read_text()
    )


ROLL = "e0000000000000000000000000000003"


def test_compact_diary_groups_and_totals(diary):
    result = compact_diary(diary)
    assert len(result["entries"]) == 5
    assert [(m["meal_type"], m["title"]) for m in result["meals"]] == [
        ("1", "Snídaně"),
        ("5", "Večeře"),
    ]
    breakfast, dinner = result["meals"]
    assert breakfast["totals"]["energy_kcal"] == pytest.approx(184.56)
    assert breakfast["totals"]["protein_g"] == pytest.approx(5.67)
    assert breakfast["totals"]["fiber_g"] is None  # the tea has no fiber value
    assert dinner["totals"]["energy_kcal"] == pytest.approx(697.5)
    assert result["totals"]["energy_kcal"] == pytest.approx(882.06)
    assert result["totals"]["carbohydrate_g"] == pytest.approx(77.65)
    roll = next(e for e in result["entries"] if e["entry_guid"] == ROLL)
    assert roll["food_guid"] == "f000000000000002"
    assert (roll["unit"], roll["quantity"], roll["quantity_unit"]) == (
        "1,5 x kus (42 g)",
        63,
        "g",
    )
    beer = result["entries"][-1]
    assert (beer["quantity"], beer["quantity_unit"]) == (1500, "ml")
    assert [e["is_recipe"] for e in result["entries"]] == [True, False, False, False, False]


def test_compact_diary_of_empty_day(diary):
    for meal in diary["data"]["times"]:
        meal["foodstuff"] = []
    result = compact_diary(diary)
    assert result["entries"] == result["meals"] == []
    assert result["totals"]["energy_kcal"] == 0


def test_compact_adapter_keeps_missing_nutrients_unknown(diary):
    diary["data"]["times"][0]["foodstuff"][2]["protein"] = None
    result = compact_diary(diary)
    assert result["meals"][0]["totals"]["protein_g"] is None
    assert result["totals"]["protein_g"] is None
    assert result["meals"][1]["totals"]["protein_g"] == pytest.approx(5.375)


def test_compact_adapter_converts_kilojoules(diary):
    row = diary["data"]["times"][0]["foodstuff"][2]
    row.update(energy="753,12", energyUnit="kJ")
    roll = compact_diary(diary)["entries"][2]
    assert roll["energy_kcal"] == pytest.approx(180)


def test_compact_adapter_blocks_duplicate_ids(diary):
    rows = diary["data"]["times"][0]["foodstuff"]
    rows[1]["id"] = rows[0]["id"]
    with pytest.raises(ValueError, match="Duplicate diary entry"):
        compact_diary(diary)


@pytest.mark.parametrize(
    "change",
    [{"energy": "-1"}, {"energy": "abc"}, {"energyUnit": "cal"}, {"title": ""}],
)
def test_compact_adapter_rejects_invalid_rows(diary, change):
    diary["data"]["times"][0]["foodstuff"][2].update(change)
    with pytest.raises(ValueError):
        compact_diary(diary)


async def test_delete_preview_checks_membership_but_commit_is_blocked(
    client, diary, monkeypatch
):
    detail = AsyncMock(return_value=diary)
    request = AsyncMock()
    monkeypatch.setattr(client, "get_diary_detail", detail)
    monkeypatch.setattr(client, "_request_with_reauth", request)
    preview = await client.delete_diary_entry(date(2026, 1, 1), ROLL)
    assert preview["entry"]["title"] == "Roll"
    assert preview["write_supported"] is False
    assert preview["mode"] == "preview"
    with pytest.raises(KalorickeTabulkyError, match="require verification"):
        await client.delete_diary_entry(date(2026, 1, 1), ROLL, commit=True)
    with pytest.raises(KalorickeTabulkyError, match="does not exist"):
        await client.delete_diary_entry(date(2026, 1, 1), "missing")
    request.assert_not_called()


async def test_range_does_not_hide_detail_read_failure(client, monkeypatch):
    async def request(method, url, **kwargs):
        if "/user/diary/" in url and "/summary/" not in url:
            raise KalorickeTabulkyError("fixture detail unavailable")
        return {"code": 0, "data": {}}

    monkeypatch.setattr(client, "_request_with_reauth", request)
    with pytest.raises(KalorickeTabulkyError, match="detail unavailable"):
        await client.get_diary_range(date(2026, 1, 1), date(2026, 1, 1))


def test_range_logged_flag_reads_live_diary_detail(diary):
    data = SummaryData([], {}, {"detail": diary["data"]})
    assert range_day(date(2026, 11, 7), data)["logged"] is True
    for meal in diary["data"]["times"]:
        meal["foodstuff"] = []
    assert range_day(date(2026, 11, 7), data)["logged"] is False
