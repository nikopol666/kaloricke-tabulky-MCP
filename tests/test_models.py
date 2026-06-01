from __future__ import annotations

import pytest
from pydantic import ValidationError

from kaloricke_tabulky_mcp.models import (
    BatchFoodItem,
    CreateCustomRecipeRequest,
    ListCustomRecipesRequest,
    RecordFoodBatchRequest,
    RecordFoodRequest,
)


def test_record_food_requires_query_or_guid() -> None:
    with pytest.raises(ValidationError, match="Set query or food_guid"):
        RecordFoodRequest(account="personal", date="2026-05-18", amount=100)


def test_record_food_defaults_to_preview() -> None:
    request = RecordFoodRequest(
        account="personal",
        date="2026-05-18",
        query="voda",
        amount=250,
        unit="ml",
    )

    assert request.commit is False


def test_list_custom_recipes_defaults() -> None:
    request = ListCustomRecipesRequest(account="personal")

    assert request.query == ""
    assert request.page == 0
    assert request.limit == 50


def test_batch_rejects_account_override() -> None:
    with pytest.raises(ValidationError, match="must not override account"):
        RecordFoodBatchRequest(
            account="personal",
            date="2026-05-18",
            items=[{"account": "other", "name": "rýže", "amount": 100, "unit": "g"}],
        )


def test_batch_rejects_conflicting_name_and_query() -> None:
    with pytest.raises(ValidationError, match="conflicting"):
        BatchFoodItem(name="rýže", query="kuře", amount=100, unit="g")


def test_batch_rejects_non_positive_amount() -> None:
    with pytest.raises(ValidationError, match="positive"):
        BatchFoodItem(name="rýže", amount=0, unit="g")


def test_create_custom_recipe_defaults_to_preview() -> None:
    request = CreateCustomRecipeRequest(
        account="personal",
        title="Test recept",
        ingredients=[{"name": "rýže", "amount": 100, "unit": "g"}],
        servings=2,
    )

    assert request.commit is False
    assert request.visibility == "private"
    assert request.include_payload is False


def test_create_custom_recipe_rejects_invalid_visibility() -> None:
    with pytest.raises(ValidationError):
        CreateCustomRecipeRequest(
            account="personal",
            title="Test recept",
            ingredients=[{"name": "rýže", "amount": 100, "unit": "g"}],
            visibility="shared",
        )
