from __future__ import annotations

import pytest
from pydantic import ValidationError

from kaloricke_tabulky_mcp.models import (
    BatchFoodItem,
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

