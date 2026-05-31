from __future__ import annotations

from kaloricke_tabulky_mcp.client import (
    _extract_candidate_endpoints,
    _extract_probe_forms,
    _extract_probe_scripts,
    _meal_type_from_time,
    _normalize_search_result,
    _safe_probe_snippet,
)


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


def test_extract_candidate_endpoints() -> None:
    endpoints = _extract_candidate_endpoints(
        """
        $http.post(root + 'user/settings/meal/save?format=json', payload)
        action="/user/settings/meal/detail/123"
        '/user/meal/foodstuff/add?format=json'
        """
    )

    assert "user/settings/meal/save?format=<redacted>" in endpoints
    assert "/user/settings/meal/detail/123" in endpoints
    assert "/user/meal/foodstuff/add?format=<redacted>" in endpoints


def test_extract_probe_forms_returns_metadata_without_values() -> None:
    forms = _extract_probe_forms(
        """
        <form method="post" action="/user/settings/meal/save?format=json">
          <input type="hidden" name="csrf" value="secret">
          <input name="title" value="Recipe name">
        </form>
        """
    )

    assert forms == [
        {
            "method": "post",
            "action": "https://www.kaloricketabulky.cz/user/settings/meal/save?format=<redacted>",
            "input_names": ["csrf", "title"],
        }
    ]


def test_extract_probe_scripts_normalizes_local_urls() -> None:
    scripts = _extract_probe_scripts("""<script src="/resources/js/user-meal.js"></script>""")

    assert scripts == ["https://www.kaloricketabulky.cz/resources/js/user-meal.js"]


def test_safe_probe_snippet_masks_email() -> None:
    snippet = _safe_probe_snippet("user/settings/meal test@example.com")

    assert "test@example.com" not in snippet
    assert "t***@example.com" in snippet
