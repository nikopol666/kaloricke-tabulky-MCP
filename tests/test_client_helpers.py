from __future__ import annotations

from kaloricke_tabulky_mcp.client import _meal_type_from_time, _normalize_search_result


def test_meal_type_from_time() -> None:
    assert _meal_type_from_time("08:00") == "1"
    assert _meal_type_from_time("12:00") == "3"
    assert _meal_type_from_time("18:00") == "5"
    assert _meal_type_from_time("23:00") == "6"


def test_normalize_search_result() -> None:
    result = _normalize_search_result(
        {
            "id": "abc",
            "title": "Rýže",
            "clazz": "foodstuff",
            "value": "123,5",
            "energyUnit": "kcal",
            "brandName": "Brand",
        }
    )

    assert result["food_guid"] == "abc"
    assert result["energy"] == 123.5
    assert result["brand_name"] == "Brand"

