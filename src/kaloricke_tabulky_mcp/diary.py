"""Duplicate-check normalization of the KT diary detail payload (not a public API).

The live ``user/diary/{date}/get`` payload groups diary rows by meal::

    data.times[]  = {"id": "1".."6", "title": ..., "foodstuff": [row, ...], ...}
    data.activities[], data.notes[], data.*Total  (ignored)
    row = {"id": <diary entry ID>, "guidType": <foodstuff or custom recipe GUID>,
           "title": ..., "time": "HH:MM", "unit": "1,5 x kus (42 g)", ...}

Rows carry no unit GUID or unit multiplier, only the displayed ``unit`` text
(``"kus (88 g)"``, ``"3 x plátek (17 g)"``, ``"50 g"``, ``"306,23 x 1 g"``).
Portions are therefore compared by total quantity (g/ml) parsed from that text,
or by unit label and count when the label states no quantity. A custom recipe
serving is shown as its total weight in grams.

Unknown or incomplete structures raise ``ValueError`` so callers block the
write instead of silently skipping the duplicate guard.
"""

from __future__ import annotations

import re
import unicodedata
from math import isclose, isfinite
from typing import Any

MEAL_IDS = frozenset({"1", "2", "3", "4", "5", "6"})

_NUMBER = r"\d[\d  ]*(?:[.,]\d+)?"
_COUNT_PREFIX = re.compile(rf"^\s*({_NUMBER})\s*[x×]\s+(.+?)\s*$", re.IGNORECASE)
_QUANTITY = re.compile(rf"({_NUMBER})\s*(kg|g|ml|l)\b", re.IGNORECASE)
_SCALE = {"kg": ("g", 1000.0), "g": ("g", 1.0), "l": ("ml", 1000.0), "ml": ("ml", 1.0)}
# Diary counts are displayed with two decimals ("1,33 x kus").
_COUNT_TOLERANCE = 0.006


def _maybe_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            return None
    return number if isfinite(number) else None


def _positive(value: Any, what: str) -> float:
    number = _maybe_number(value)
    if number is None or number <= 0:
        raise ValueError(f"Invalid {what} for duplicate check")
    return number


def _normalize_label(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace(",", ".")
    return " ".join(text.split())


def unit_portion(label: str, count: Any) -> dict[str, Any]:
    """Describe ``count`` x ``label`` with its total g/ml quantity when stated."""
    if not isinstance(label, str) or not label.strip():
        raise ValueError("Missing unit label for duplicate check")
    amount = _positive(count, "unit count")
    quantity = dimension = None
    matches = list(_QUANTITY.finditer(label))
    if matches:
        size = _positive(matches[-1].group(1), "unit size")
        dimension, scale = _SCALE[matches[-1].group(2).lower()]
        quantity = amount * size * scale
    return {
        "label": _normalize_label(label),
        "count": amount,
        "quantity": quantity,
        "dimension": dimension,
    }


def parse_unit_text(text: Any) -> dict[str, Any]:
    """Parse a diary ``unit`` text such as ``"1,5 x kus (42 g)"``."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Diary entry lacks unit text for duplicate check")
    match = _COUNT_PREFIX.match(text)
    if match:
        return unit_portion(match.group(2), match.group(1))
    return unit_portion(text, 1)


def food_portion(payload: dict[str, Any]) -> dict[str, Any]:
    """Portion selected in a foodstuff add form (``unitGuid`` x ``multiplier``)."""
    unit_guid = payload.get("unitGuid")
    option = next(
        (
            item
            for item in payload.get("unitOptions") or []
            if isinstance(item, dict)
            and unit_guid is not None
            and str(item.get("id")) == str(unit_guid)
        ),
        None,
    )
    if option is None:
        raise ValueError("Selected unit is not among the food's unit options")
    return unit_portion(str(option.get("title") or ""), payload.get("multiplier"))


def recipe_portion(payload: dict[str, Any]) -> dict[str, Any]:
    """Portion of a recipe serving form; the diary shows it in total grams."""
    ingredients = payload.get("foodstuff")
    if not isinstance(ingredients, list) or not ingredients:
        raise ValueError("Recipe serving lacks ingredients to compute its weight")
    total = 0.0
    for item in ingredients:
        if not isinstance(item, dict):
            raise ValueError("Unsupported recipe ingredient structure")
        if not item.get("selected", True):
            continue
        count = _maybe_number(item.get("count"))
        if count is None:
            count = _maybe_number(item.get("countOriginal"))
        unit = next(
            (
                option
                for option in item.get("units") or []
                if isinstance(option, dict)
                and option.get("id") == item.get("selectedUnitGuid")
            ),
            None,
        )
        size = _maybe_number(unit.get("multiplier")) if unit else 1.0
        if count is None or count < 0 or size is None or size <= 0:
            raise ValueError("Recipe ingredient lacks a usable weight")
        total += count * size
    if total <= 0:
        raise ValueError("Recipe serving has no weight")
    portion = unit_portion("1 g", total)
    label = next(
        (
            option.get("title")
            for option in payload.get("units") or []
            if isinstance(option, dict)
            and option.get("id") == payload.get("selectedUnitGuid")
        ),
        None,
    )
    count = _maybe_number(payload.get("selectedUnitMultiplier"))
    if isinstance(label, str) and label.strip() and count and count > 0:
        portion.update(label=_normalize_label(label), count=count)
    return portion


def duplicate_entries(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the diary rows of one day; raise ``ValueError`` on unknown shapes."""
    data = detail.get("data") if isinstance(detail, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("times"), list):
        raise ValueError(
            "Unrecognized diary structure; duplicate check cannot be completed"
        )
    entries: list[dict[str, Any]] = []
    for meal in data["times"]:
        if not isinstance(meal, dict) or str(meal.get("id")) not in MEAL_IDS:
            raise ValueError("Unrecognized diary meal identifier")
        rows = meal.get("foodstuff")
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise ValueError("Unsupported diary meal rows")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Unsupported diary entry structure")
            entry_guid, food_guid = row.get("id"), row.get("guidType")
            if not all(str(v or "").strip() for v in (entry_guid, food_guid)):
                raise ValueError(
                    "Diary entry lacks entry or food identity for duplicate check"
                )
            entries.append(
                {
                    "entry_guid": str(entry_guid),
                    "food_guid": str(food_guid),
                    "meal_type": str(meal["id"]),
                    "title": row.get("title"),
                    "time": row.get("time"),
                    "unit": row.get("unit"),
                    "portion": parse_unit_text(row.get("unit")),
                }
            )
    return entries


def resolved_portion(resolved: dict[str, Any]) -> dict[str, Any]:
    payload = resolved.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Resolved item lacks a form payload for duplicate check")
    if resolved.get("kind") == "recipe":
        return recipe_portion(payload)
    return food_portion(payload)


def resolved_entry(resolved: dict[str, Any], entry_guid: str | None) -> dict[str, Any]:
    if str(resolved.get("meal_type")) not in MEAL_IDS:
        raise ValueError("Resolved item has no valid meal for duplicate check")
    return {
        "entry_guid": entry_guid,
        "food_guid": str(resolved["food_guid"]),
        "meal_type": str(resolved["meal_type"]),
        "title": resolved.get("title"),
        "time": resolved.get("time"),
        "portion": resolved_portion(resolved),
    }


def same_portion(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["label"] == b["label"]:
        return isclose(a["count"], b["count"], rel_tol=0, abs_tol=_COUNT_TOLERANCE)
    if (
        a["quantity"] is not None
        and b["quantity"] is not None
        and a["dimension"] == b["dimension"]
    ):
        return isclose(a["quantity"], b["quantity"], rel_tol=1e-3, abs_tol=0.01)
    return False


def find_duplicate(
    wanted: dict[str, Any], entries: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the first diary entry matching ``resolved_entry(...)`` output."""
    return next(
        (
            entry
            for entry in entries
            if entry["food_guid"] == wanted["food_guid"]
            and entry["meal_type"] == wanted["meal_type"]
            and same_portion(entry["portion"], wanted["portion"])
        ),
        None,
    )
