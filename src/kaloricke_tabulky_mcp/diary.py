"""Conservative normalization for duplicate checks, not a public diary API.

Unknown or incomplete diary rows must block a write rather than silently defeat
the duplicate guard. IDs refer to diary entries, separately from food/recipe IDs.
"""

from math import isclose, isfinite
from typing import Any


def _first(row: dict[str, Any], *keys: str) -> Any:
    return next((row[k] for k in keys if row.get(k) is not None), None)


def _number(value: Any) -> float:
    number = float(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    if not isfinite(number) or number <= 0:
        raise ValueError("Invalid diary quantity")
    return number


def _diary_entries(detail: dict[str, Any], *, display: bool = False) -> list[dict[str, Any]]:
    """Accept explicit foodstuff/entries collections, optionally grouped by meal.

    These field mappings are tested with synthetic fixtures. Live KT payloads
    still need manual verification; unsupported shapes fail closed.
    """
    entries: list[dict[str, Any]] = []
    found_collection = False

    def visit(value: Any, meal: Any = None, collection: bool = False) -> None:
        nonlocal found_collection
        if isinstance(value, list):
            if collection:
                found_collection = True
            for child in value:
                visit(child, meal, collection)
            return
        if not isinstance(value, dict):
            raise ValueError("Unsupported diary entry structure")
        meal = _first(value, "diaryTimeGuid", "meal_type") or meal
        food = _first(
            value,
            "foodstuffGuid",
            "guidFoodstuff",
            "food_guid",
            "recipeGuid",
            "guidRecipe",
        )
        # Some explicit entry representations keep the food GUID in `guid`.
        if food is None and _first(value, "entry_guid", "entryGuid") is not None:
            food = value.get("guid")
        if food is not None:
            entry = _first(
                value, "entry_guid", "entryGuid", "guidDiaryFoodstuff", "id", "guid"
            )
            unit = _first(value, "unit_guid", "unitGuid", "selectedUnitGuid")
            amount = _first(
                value, "multiplier", "unitCount", "selectedUnitMultiplier", "amount"
            )
            if not all(
                str(v).strip() if v is not None else False
                for v in (food, entry, meal, unit)
            ):
                raise ValueError(
                    "Diary entry lacks identity, meal or unit for duplicate check"
                )
            if str(meal) not in {"1", "2", "3", "4", "5", "6"}:
                raise ValueError("Unrecognized diary meal identifier")
            normalized = {
                "entry_guid": str(entry), "food_guid": str(food), "meal_type": str(meal),
                "unit_guid": str(unit), "multiplier": _number(amount),
            }
            if display:
                normalized = {
                    "entry_guid": str(entry), "food_guid": str(food), "meal_type": str(meal),
                    "time": value.get("time"), "title": value.get("title"),
                    "amount": _number(amount), "unit": value.get("unit"),
                    **{key: _optional_total(value.get(key)) for key in COMPACT_NUTRIENTS},
                }
                if not isinstance(normalized["title"], str) or not normalized["title"].strip():
                    raise ValueError("Missing diary entry title")
                if not isinstance(normalized["unit"], str) or not normalized["unit"].strip():
                    raise ValueError("Missing diary unit label")
            entries.append(normalized)
            return  # A recipe's ingredients are not separate diary entries.
        containers = [
            k for k in ("foodstuff", "entries", "meals", "items") if k in value
        ]
        if containers:
            if any(
                key not in containers and isinstance(child, (dict, list)) and child
                for key, child in value.items()
            ):
                raise ValueError("Unrecognized sibling diary collection")
            for key in containers:
                if not isinstance(value[key], (dict, list)):
                    raise ValueError("Unsupported diary collection")
                visit(value[key], meal, True)
        elif collection:
            raise ValueError(
                "Unrecognized diary row; duplicate check cannot be completed"
            )

    if "data" not in detail or not isinstance(detail["data"], (dict, list)):
        raise ValueError("Missing diary data for duplicate check")
    visit(detail["data"], collection=isinstance(detail["data"], list))
    if not found_collection:
        raise ValueError(
            "Unrecognized diary structure; duplicate check cannot be completed"
        )
    return entries


def resolved_entry(resolved: dict[str, Any], entry_guid: str | None) -> dict[str, Any]:
    return {
        "entry_guid": entry_guid,
        "food_guid": str(resolved["food_guid"]),
        "meal_type": str(resolved["meal_type"]),
        "unit_guid": str(resolved["unit_guid"]),
        "multiplier": _number(resolved["multiplier"]),
    }


def find_duplicate(
    resolved: dict[str, Any], entries: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if not resolved.get("unit_guid") or resolved.get("multiplier") is None:
        raise ValueError("Resolved item lacks unit/quantity for duplicate check")
    wanted = resolved_entry(resolved, None)
    return next(
        (
            entry
            for entry in entries
            if all(
                entry[key] == wanted[key]
                for key in ("food_guid", "meal_type", "unit_guid")
            )
            and isclose(
                entry["multiplier"], wanted["multiplier"], rel_tol=1e-9, abs_tol=1e-9
            )
        ),
        None,
    )


COMPACT_NUTRIENTS = ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g")


def duplicate_entries(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return _diary_entries(detail)


def _optional_total(value: Any) -> float | None:
    if value is None:
        return None
    number = float(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    if not isfinite(number) or number < 0:
        raise ValueError("Invalid diary nutrient total")
    return number


def _totals(entries: list[dict[str, Any]]) -> dict[str, float | None]:
    return {
        key: sum(row[key] for row in entries) if all(row[key] is not None for row in entries) else None
        for key in COMPACT_NUTRIENTS
    }


def compact_diary(detail: dict[str, Any]) -> dict[str, Any]:
    """Draft adapter: nutrient totals require explicit *_kcal/*_g fields.

    Do not guess whether a raw KT nutrient field means per-gram or entry total.
    A live anonymized fixture is required to extend/verify these mappings.
    """
    entries = _diary_entries(detail, display=True)
    seen = set()
    for entry in entries:
        if entry["entry_guid"] in seen:
            raise ValueError("Duplicate diary entry identifiers")
        seen.add(entry["entry_guid"])
    meals = []
    for meal_type in sorted({row["meal_type"] for row in entries}):
        rows = [row for row in entries if row["meal_type"] == meal_type]
        meals.append({"meal_type": meal_type, "entries": rows, "totals": _totals(rows)})
    return {"entries": entries, "meals": meals, "totals": _totals(entries)}
