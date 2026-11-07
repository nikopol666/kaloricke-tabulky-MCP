"""Compact daily metrics and averages, preserving missing data as null."""

from datetime import date
from math import isfinite
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import SummaryData, SummaryMetric


FIELDS = {
    "energy_kcal": (
        "energy",
        "energy_kcal",
        "energykcal",
        "energie",
        "calories",
        "kcal",
        "kj",
        "total",
    ),
    "protein_g": ("protein", "proteins", "bilkoviny"),
    "carbohydrate_g": ("carbohydrate", "carbohydrates", "carbs", "sacharidy"),
    "fat_g": ("fat", "fats", "tuky"),
    "fiber_g": ("fiber", "fibre", "vlaknina"),
    "sugar_g": ("sugar", "sugars", "cukry", "z_toho_cukry"),
    "salt_g": ("salt", "sul"),
    "energy_target_kcal": ("energy_target",),
    "weight_kg": ("weight",),
}


def _value(metric: "SummaryMetric", field: str, *, goal: bool = False) -> float | None:
    value = metric.goal if goal else metric.value
    if value is None:
        return None
    if not isfinite(value):
        raise ValueError("Non-finite diary metric")
    unit = (
        metric.unit
        or ("kcal" if "kcal" in field else "kg" if field == "weight_kg" else "g")
    ).casefold()
    factors = (
        {"kcal": 1, "kj": 1 / 4.184}
        if "kcal" in field
        else (
            {"kg": 1, "g": 0.001}
            if field == "weight_kg"
            else {"g": 1, "mg": 0.001, "kg": 1000}
        )
    )
    if unit not in factors:
        raise ValueError(f"Unsupported unit for {field}: {unit}")
    return value * factors[unit]


def _has_entries(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"foodstuff", "entries"} and isinstance(child, list) and child:
                return True
            if isinstance(child, (dict, list)) and _has_entries(child):
                return True
    elif isinstance(value, list):
        return any(_has_entries(child) for child in value)
    return False


def range_day(day: date, summary: "SummaryData") -> dict[str, Any]:
    result: dict[str, Any] = {"date": day.isoformat()}
    for field, aliases in FIELDS.items():
        metric = next(
            (summary.metrics[key] for key in aliases if key in summary.metrics), None
        )
        result[field] = _value(metric, field) if metric is not None else None
    if result["energy_target_kcal"] is None:
        energy = next(
            (
                summary.metrics[key]
                for key in FIELDS["energy_kcal"]
                if key in summary.metrics
            ),
            None,
        )
        if energy is not None:
            result["energy_target_kcal"] = _value(
                energy, "energy_target_kcal", goal=True
            )
    if result["weight_kg"] is None:
        # A monthly historical weight must not be assigned to every requested day.
        record = next(
            (
                item
                for item in summary.weight_records
                if item.date_label
                in {
                    day.isoformat(),
                    day.strftime("%d.%m.%Y"),
                }
            ),
            None,
        )
        if record is not None:
            if not isfinite(record.weight):
                raise ValueError("Non-finite diary weight")
            result["weight_kg"] = record.weight
    result["logged"] = _has_entries(summary.raw.get("detail")) or any(
        result[field] is not None and result[field] > 0
        for field in FIELDS
        if field not in {"energy_target_kcal", "weight_kg"}
    )
    return result


def range_summary(days: list[dict[str, Any]]) -> dict[str, Any]:
    logged = [day for day in days if day["logged"]]
    averages = {}
    counts = {}
    for field in FIELDS:
        values = [day[field] for day in logged if day[field] is not None]
        counts[field] = len(values)
        averages[field] = sum(values) / len(values) if values else None
    return {
        "days_with_record": len(logged),
        "empty_days": len(days) - len(logged),
        "averages": averages,
        "average_sample_counts": counts,
    }
