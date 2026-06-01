"""Pydantic models for tool inputs."""

from __future__ import annotations

from datetime import date as Date, datetime
from math import isfinite
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FoodKind = Literal["food", "drink"]


def parse_date(value: str | Date | None) -> Date:
    if isinstance(value, Date):
        return value
    if value is None or value == "":
        return Date.today()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError("date must use YYYY-MM-DD or DD.MM.YYYY")


def parse_time(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?", text)
    if not match:
        raise ValueError("time must use HH:MM")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def validate_amount(value: float | None) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if not isfinite(numeric) or numeric <= 0:
        raise ValueError("amount must be a positive finite number")
    return numeric


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchFoodRequest(StrictModel):
    account: str | None = None
    query: str = Field(min_length=1)
    kind: FoodKind = "food"
    page: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=50)


class ListCustomRecipesRequest(StrictModel):
    account: str = Field(min_length=1)
    query: str = ""
    page: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class RecordFoodRequest(StrictModel):
    account: str = Field(min_length=1)
    date: Date = Field(default_factory=Date.today)
    meal_type: str | None = None
    time: str | None = None
    kind: FoodKind = "food"
    query: str | None = None
    food_guid: str | None = None
    amount: float | None = None
    unit: str | None = None
    unit_guid: str | None = None
    commit: bool = False

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value: str | Date | None) -> Date:
        return parse_date(value)

    @field_validator("time", mode="before")
    @classmethod
    def _time(cls, value: str | None) -> str | None:
        return parse_time(value)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, value: float | None) -> float | None:
        return validate_amount(value)

    @model_validator(mode="after")
    def _has_food_reference(self) -> "RecordFoodRequest":
        if not self.query and not self.food_guid:
            raise ValueError("Set query or food_guid")
        return self


class BatchFoodItem(StrictModel):
    name: str | None = None
    query: str | None = None
    food_guid: str | None = None
    amount: float
    unit: str | None = None
    unit_guid: str | None = None
    kind: FoodKind = "food"
    meal_type: str | None = None
    time: str | None = None
    source: str | None = None
    source_id: str | None = None
    note: str | None = None

    @field_validator("time", mode="before")
    @classmethod
    def _time(cls, value: str | None) -> str | None:
        return parse_time(value)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, value: float | None) -> float:
        amount = validate_amount(value)
        if amount is None:
            raise ValueError("amount is required")
        return amount

    @model_validator(mode="after")
    def _valid_reference(self) -> "BatchFoodItem":
        if self.name and self.query and self.name.strip() != self.query.strip():
            raise ValueError("Use either name or query, not conflicting values")
        if not self.food_guid and not (self.query or self.name):
            raise ValueError("Set food_guid, query, or name")
        return self

    def effective_query(self) -> str | None:
        return self.query or self.name


class RecordFoodBatchRequest(StrictModel):
    account: str = Field(min_length=1)
    date: Date = Field(default_factory=Date.today)
    items: list[BatchFoodItem] = Field(min_length=1, max_length=100)
    default_meal_type: str | None = None
    default_time: str | None = None
    commit: bool = False
    continue_on_error: bool = True

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value: str | Date | None) -> Date:
        return parse_date(value)

    @field_validator("default_time", mode="before")
    @classmethod
    def _time(cls, value: str | None) -> str | None:
        return parse_time(value)

    @model_validator(mode="before")
    @classmethod
    def _reject_item_account_override(cls, data: object) -> object:
        if isinstance(data, dict):
            for item in data.get("items", []) or []:
                if isinstance(item, dict) and "account" in item:
                    raise ValueError("Batch items must not override account")
        return data


class RecipeIngredientItem(BatchFoodItem):
    pass


class PrepareRecipeImportRequest(StrictModel):
    account: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    ingredients: list[RecipeIngredientItem] = Field(min_length=1, max_length=100)
    servings: float | None = None
    total_weight_g: float | None = None
    source: str | None = None
    source_id: str | None = None
    continue_on_error: bool = True

    @field_validator("servings", "total_weight_g", mode="before")
    @classmethod
    def _positive_optional(cls, value: float | None) -> float | None:
        return validate_amount(value)

    @model_validator(mode="before")
    @classmethod
    def _reject_item_account_override(cls, data: object) -> object:
        if isinstance(data, dict):
            for item in data.get("ingredients", []) or []:
                if isinstance(item, dict) and "account" in item:
                    raise ValueError("Recipe ingredients must not override account")
        return data


class CreateCustomRecipeRequest(PrepareRecipeImportRequest):
    preparation_time_minutes: int | None = Field(default=None, ge=0, le=1440)
    visibility: Literal["private", "public"] = "private"
    description: list[str] | None = None
    include_payload: bool = False
    commit: bool = False


class RecordRecipeServingRequest(StrictModel):
    account: str = Field(min_length=1)
    date: Date = Field(default_factory=Date.today)
    meal_type: str | None = None
    time: str | None = None
    query: str | None = None
    recipe_guid: str | None = None
    food_guid: str | None = None
    amount: float | None = None
    unit: str | None = None
    unit_guid: str | None = None
    commit: bool = False

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value: str | Date | None) -> Date:
        return parse_date(value)

    @field_validator("time", mode="before")
    @classmethod
    def _time(cls, value: str | None) -> str | None:
        return parse_time(value)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, value: float | None) -> float | None:
        return validate_amount(value)

    @model_validator(mode="after")
    def _has_recipe_reference(self) -> "RecordRecipeServingRequest":
        if not self.recipe_guid and not self.food_guid and not self.query:
            raise ValueError("Set recipe_guid, food_guid, or query")
        return self


class DiarySummaryRequest(StrictModel):
    account: str | None = None
    date: Date = Field(default_factory=Date.today)

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value: str | Date | None) -> Date:
        return parse_date(value)


class RecordWeightRequest(StrictModel):
    account: str = Field(min_length=1)
    date: Date = Field(default_factory=Date.today)
    weight_kg: float
    commit: bool = False

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value: str | Date | None) -> Date:
        return parse_date(value)

    @field_validator("weight_kg", mode="before")
    @classmethod
    def _weight(cls, value: float) -> float:
        numeric = validate_amount(value)
        if numeric is None:
            raise ValueError("weight_kg is required")
        return numeric
