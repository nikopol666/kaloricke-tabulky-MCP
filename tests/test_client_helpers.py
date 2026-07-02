from __future__ import annotations

from kaloricke_tabulky_mcp.client import (
    AccountCredentials,
    LOGIN_URL,
    _append_query_param,
    _auth_failure_message,
    _default_recipe_tags,
    _apply_recipe_serving_selection,
    _apply_unit_selection,
    _normalize_custom_recipe,
    _recipe_item_from_resolved,
    _recipe_portions,
    _extract_candidate_endpoints,
    _extract_login_action,
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


def test_normalize_custom_recipe() -> None:
    result = _normalize_custom_recipe(
        {
            "guid": "recipe-guid",
            "title": "TEST",
            "energy": "216",
            "energyUnit": "kcal",
            "visibility": "private",
            "portions": 4.0,
        }
    )

    assert result == {
        "recipe_guid": "recipe-guid",
        "title": "TEST",
        "energy": 216.0,
        "energy_unit": "kcal",
        "visibility": "private",
        "portions": 4.0,
        "url": None,
        "in_use": None,
    }


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


def test_extract_login_action_from_hidden_input() -> None:
    assert (
        _extract_login_action(
            '<input type="hidden" id="loginUrl" action="/login/create?=&format=json"/>'
        )
        == "/login/create?=&format=json"
    )


def test_append_query_param_preserves_existing_query() -> None:
    assert (
        _append_query_param("https://example.test/login/create?=&format=json", "voucher=false")
        == "https://example.test/login/create?=&format=json&voucher=false"
    )
    assert _append_query_param(f"{LOGIN_URL}&voucher=true", "voucher=false").endswith(
        "voucher=true"
    )


def test_auth_failure_message_exposes_only_safe_credential_metadata() -> None:
    message = _auth_failure_message(
        AccountCredentials(alias="luky", email="test@example.com", password="secret"),
        [{"url": LOGIN_URL, "status": 200, "code": 10}],
    )

    assert "t***@example.com" in message
    assert "password_len=6" in message
    assert "secret" not in message
    assert "test@example.com" not in message


def test_extract_probe_scripts_normalizes_local_urls() -> None:
    scripts = _extract_probe_scripts("""<script src="/resources/js/user-meal.js"></script>""")

    assert scripts == ["https://www.kaloricketabulky.cz/resources/js/user-meal.js"]


def test_safe_probe_snippet_masks_email() -> None:
    snippet = _safe_probe_snippet("user/settings/meal test@example.com")

    assert "test@example.com" not in snippet
    assert "t***@example.com" in snippet


def test_recipe_item_from_resolved_uses_diary_payload_shape() -> None:
    item = _recipe_item_from_resolved(
        {
            "food_guid": "food-guid-1",
            "title": "kuřecí prsa",
            "payload": {
                "title": "kuřecí prsa",
                "energy": 1.056,
                "energyUnit": "kcal",
                "protein": 0.231,
                "carbohydrate": 0,
                "fat": 0.015,
                "unitGuid": "0000000000000001",
                "multiplier": 162,
                "unitOptions": [
                    {"id": "120g", "title": "120 g", "multiplier": 120},
                    {"id": "0000000000000001", "title": "1 g", "multiplier": 1},
                ],
            },
        }
    )

    assert item["guid"] == "food-guid-1"
    assert item["locked"] is True
    assert item["unitCount"] == 162
    assert item["selectedUnitGuid"] == "0000000000000001"
    assert item["units"] == [
        {"id": "120g", "title": "120 g", "multiplier": 120},
        {"id": "0000000000000001", "title": "1 g", "multiplier": 1},
    ]


def test_recipe_portions_are_sent_as_string() -> None:
    assert _recipe_portions(None) == "1"
    assert _recipe_portions(4) == "4"
    assert _recipe_portions(2.5) == "2.5"


def test_default_recipe_tags_match_web_payload_shape() -> None:
    tags = _default_recipe_tags()

    assert tags
    assert tags[0]["guid"] == tags[0]["guidTag"] == tags[0]["guidRecipe"]
    assert tags[0]["selected"] is False


def test_apply_unit_selection_tolerates_null_unit_options() -> None:
    payload = {"unitOptions": None}

    _apply_unit_selection(payload, amount=1, unit="porce", unit_guid=None)

    assert payload["multiplier"] == 1


def test_apply_recipe_serving_selection_scales_portions() -> None:
    payload = {
        "selectedUnitGuid": "portion",
        "selectedUnitMultiplier": 1,
        "portionsMax": 4,
        "units": [{"id": "portion", "title": "porce", "multiplier": -2}],
        "foodstuff": [
            {
                "selected": True,
                "count": 100,
                "selectedUnitGuid": "g",
                "units": [{"id": "g", "title": "1 g", "multiplier": 1}],
            },
            {
                "selected": True,
                "count": 20,
                "selectedUnitGuid": "g",
                "units": [{"id": "g", "title": "1 g", "multiplier": 1}],
            },
        ],
    }

    _apply_recipe_serving_selection(payload, amount=1, unit="porce", unit_guid=None)

    assert payload["selectedUnitGuid"] == "portion"
    assert payload["selectedUnitMultiplier"] == 1
    assert payload["foodstuff"][0]["count"] == 25
    assert payload["foodstuff"][1]["count"] == 5
