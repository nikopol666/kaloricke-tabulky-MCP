from __future__ import annotations

from typing import Any

import pytest

from kaloricke_tabulky_mcp.tools import setup_tools


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class FakeClient:
    alias = "personal"

    def __init__(self) -> None:
        self.write_calls = 0
        self.recipe_write_calls = 0

    async def resolve_food(self, **kwargs):
        return {
            "food_guid": kwargs.get("food_guid") or "guid-1",
            "title": "Rýže",
            "date": "18.05.2026",
            "time": kwargs.get("target_time"),
            "meal_type": "3",
            "unit_guid": "unit-1",
            "multiplier": kwargs.get("amount"),
            "search_result": {"food_guid": "guid-1"},
            "payload": {
                "title": "Rýže",
                "energy": 1.2,
                "energyUnit": "kcal",
                "protein": 0.02,
                "carbohydrate": 0.25,
                "fat": 0.01,
                "unitGuid": "unit-1",
                "multiplier": kwargs.get("amount"),
                "unitOptions": [{"id": "unit-1", "title": "1 g", "multiplier": 1}],
            },
        }

    async def record_resolved_food(self, resolved):
        self.write_calls += 1
        return {"message": "ok", "food_guid": resolved["food_guid"]}

    def build_custom_recipe_payload(self, **kwargs):
        return {
            "title": kwargs["title"],
            "portions": str(int(kwargs["servings"] or 1)),
            "items": [
                {
                    "guid": item["food_guid"],
                    "title": item["title"],
                    "unitCount": item["payload"]["multiplier"],
                    "selectedUnitGuid": item["payload"]["unitGuid"],
                }
                for item in kwargs["resolved_ingredients"]
            ],
        }

    async def create_custom_recipe(self, **kwargs):
        self.recipe_write_calls += 1
        return {
            "message": "[recipe.save.success (cs)]",
            "recipe_guid": "recipe-guid-1",
            "title": kwargs["title"],
            "ingredient_count": len(kwargs["resolved_ingredients"]),
        }

    async def list_custom_recipes(self, **kwargs):
        return {
            "account": self.alias,
            "query": kwargs.get("query", ""),
            "page": kwargs.get("page", 0),
            "limit": kwargs.get("limit", 50),
            "count": 1,
            "recipes": [{"recipe_guid": "recipe-guid-1", "title": "TEST"}],
        }

    async def probe_user_meal_endpoints(self):
        return {
            "account": self.alias,
            "mode": "read_only_probe",
            "probes": [],
            "candidate_endpoints": [],
        }


class FakeRegistry:
    def __init__(self) -> None:
        self.client = FakeClient()

    def safe_health(self):
        return {"configured_accounts": [{"alias": "personal", "configured": True}]}

    def list_accounts(self):
        return [{"alias": "personal", "configured": True}]

    async def check_auth(self, account):
        return {"account": account, "status": "ok"}

    def get_read_client(self, account=None):
        return self.client

    def get_write_client(self, account):
        if not account:
            raise ValueError("Write tools require explicit account")
        return self.client


@pytest.mark.asyncio
async def test_record_food_without_commit_does_not_write() -> None:
    mcp = FakeMCP()
    registry = FakeRegistry()
    setup_tools(mcp, registry)  # type: ignore[arg-type]

    result = await mcp.tools["record_food"](
        account="personal",
        date="2026-05-18",
        query="rýže",
        amount=100,
        unit="g",
    )

    assert result["mode"] == "preview"
    assert registry.client.write_calls == 0


@pytest.mark.asyncio
async def test_record_food_with_commit_writes_once() -> None:
    mcp = FakeMCP()
    registry = FakeRegistry()
    setup_tools(mcp, registry)  # type: ignore[arg-type]

    result = await mcp.tools["record_food"](
        account="personal",
        date="2026-05-18",
        query="rýže",
        amount=100,
        unit="g",
        commit=True,
    )

    assert result["status"] == "written"
    assert registry.client.write_calls == 1


@pytest.mark.asyncio
async def test_list_custom_recipes_is_read_only() -> None:
    mcp = FakeMCP()
    registry = FakeRegistry()
    setup_tools(mcp, registry)  # type: ignore[arg-type]

    result = await mcp.tools["list_custom_recipes"](account="personal", query="TEST")

    assert result["recipes"][0]["recipe_guid"] == "recipe-guid-1"
    assert registry.client.write_calls == 0
    assert registry.client.recipe_write_calls == 0


@pytest.mark.asyncio
async def test_prepare_recipe_import_is_preview_only() -> None:
    mcp = FakeMCP()
    registry = FakeRegistry()
    setup_tools(mcp, registry)  # type: ignore[arg-type]

    result = await mcp.tools["prepare_recipe_import"](
        account="personal",
        title="Test recept",
        ingredients=[
            {"name": "rýže", "amount": 100, "unit": "g", "source": "mealie"},
            {"name": "kuřecí prsa", "amount": 150, "unit": "g", "source": "mealie"},
        ],
        servings=2,
        source="mealie",
        source_id="recipe-1",
    )

    assert result["mode"] == "preview"
    assert result["write_supported"] is True
    assert result["recipe_endpoint_status"] == "verified"
    assert result["success_count"] == 2
    assert registry.client.write_calls == 0
    assert registry.client.recipe_write_calls == 0


@pytest.mark.asyncio
async def test_create_custom_recipe_without_commit_does_not_write() -> None:
    mcp = FakeMCP()
    registry = FakeRegistry()
    setup_tools(mcp, registry)  # type: ignore[arg-type]

    result = await mcp.tools["create_custom_recipe"](
        account="personal",
        title="Test recept",
        ingredients=[{"name": "rýže", "amount": 100, "unit": "g"}],
        servings=2,
    )

    assert result["mode"] == "preview"
    assert result["write_supported"] is True
    assert result["payload_summary"]["ingredients"][0]["selectedUnitGuid"] == "unit-1"
    assert "payload" not in result
    assert registry.client.recipe_write_calls == 0


@pytest.mark.asyncio
async def test_create_custom_recipe_can_include_diagnostic_payload() -> None:
    mcp = FakeMCP()
    registry = FakeRegistry()
    setup_tools(mcp, registry)  # type: ignore[arg-type]

    result = await mcp.tools["create_custom_recipe"](
        account="personal",
        title="Test recept",
        ingredients=[{"name": "rýže", "amount": 100, "unit": "g"}],
        servings=2,
        include_payload=True,
    )

    assert result["payload"]["items"][0]["selectedUnitGuid"] == "unit-1"
    assert registry.client.recipe_write_calls == 0


@pytest.mark.asyncio
async def test_create_custom_recipe_with_commit_writes_once() -> None:
    mcp = FakeMCP()
    registry = FakeRegistry()
    setup_tools(mcp, registry)  # type: ignore[arg-type]

    result = await mcp.tools["create_custom_recipe"](
        account="personal",
        title="Test recept",
        ingredients=[{"name": "rýže", "amount": 100, "unit": "g"}],
        servings=2,
        commit=True,
    )

    assert result["status"] == "written"
    assert result["recipe_guid"] == "recipe-guid-1"
    assert registry.client.recipe_write_calls == 1


@pytest.mark.asyncio
async def test_probe_user_meal_endpoints_is_read_only() -> None:
    mcp = FakeMCP()
    registry = FakeRegistry()
    setup_tools(mcp, registry)  # type: ignore[arg-type]

    result = await mcp.tools["probe_user_meal_endpoints"](account="personal")

    assert result["mode"] == "read_only_probe"
    assert registry.client.write_calls == 0


@pytest.mark.asyncio
async def test_record_recipe_serving_preview_does_not_write() -> None:
    mcp = FakeMCP()
    registry = FakeRegistry()
    setup_tools(mcp, registry)  # type: ignore[arg-type]

    result = await mcp.tools["record_recipe_serving"](
        account="personal",
        date="2026-06-01",
        query="Lasagne podle Mealie",
        amount=1,
        unit="porce",
    )

    assert result["mode"] == "preview"
    assert registry.client.write_calls == 0


@pytest.mark.asyncio
async def test_record_recipe_serving_commit_writes_once() -> None:
    mcp = FakeMCP()
    registry = FakeRegistry()
    setup_tools(mcp, registry)  # type: ignore[arg-type]

    result = await mcp.tools["record_recipe_serving"](
        account="personal",
        date="2026-06-01",
        recipe_guid="recipe-guid-1",
        amount=1,
        unit="porce",
        commit=True,
    )

    assert result["status"] == "written"
    assert registry.client.write_calls == 1
