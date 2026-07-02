"""MCP tool registration and tool implementation."""

from __future__ import annotations

import base64
import binascii
from datetime import date
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from .account_registry import AccountRegistry
from .client import KalorickeTabulkyError
from .models import (
    CreateCustomRecipeRequest,
    DiarySummaryRequest,
    ListCustomRecipesRequest,
    PrepareRecipeImportRequest,
    RecordFoodBatchRequest,
    RecordFoodRequest,
    RecordRecipeServingRequest,
    RecordWeightRequest,
    SearchFoodRequest,
    UploadCustomRecipeImageRequest,
)

logger = logging.getLogger(__name__)


def setup_tools(mcp: FastMCP, registry: AccountRegistry) -> None:
    """Register all MCP tools."""

    @mcp.tool()
    async def health_check() -> dict[str, Any]:
        """Return safe process/config health without active account login probes."""
        return {
            "status": "healthy",
            "server": "Kaloricke Tabulky MCP",
            **registry.safe_health(),
        }

    @mcp.tool()
    async def list_accounts() -> dict[str, Any]:
        """List configured account aliases without exposing secrets."""
        return {"accounts": registry.list_accounts()}

    @mcp.tool()
    async def check_account_auth(account: str) -> dict[str, Any]:
        """Run an explicit bounded authentication check for one account."""
        return await registry.check_auth(account)

    @mcp.tool()
    async def probe_user_meal_endpoints(account: str) -> dict[str, Any]:
        """Read-only probe for private KT custom meal/recipe form endpoints."""
        try:
            client = registry.get_write_client(account)
            return await client.probe_user_meal_endpoints()
        except (ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def search_food(
        query: str,
        account: str | None = None,
        kind: str = "food",
        page: int = 0,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search Kaloricke Tabulky food or drink records."""
        try:
            request = SearchFoodRequest(
                account=account, query=query, kind=kind, page=page, limit=limit
            )
            client = registry.get_read_client(request.account)
            results = await client.search_food(
                request.query, kind=request.kind, page=request.page, limit=request.limit
            )
            return {"account": client.alias, "query": request.query, "results": results}
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def list_custom_recipes(
        account: str,
        query: str = "",
        page: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List private KT custom recipes/meals and their GUIDs."""
        try:
            request = ListCustomRecipesRequest(
                account=account, query=query, page=page, limit=limit
            )
            client = registry.get_write_client(request.account)
            return await client.list_custom_recipes(
                query=request.query, page=request.page, limit=request.limit
            )
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def record_food(
        account: str,
        date: str,
        meal_type: str | None = None,
        time: str | None = None,
        kind: str = "food",
        query: str | None = None,
        food_guid: str | None = None,
        amount: float | None = None,
        unit: str | None = None,
        unit_guid: str | None = None,
        commit: bool = False,
    ) -> dict[str, Any]:
        """Preview or record a single food/drink item. Writes only with commit=true."""
        try:
            request = RecordFoodRequest(
                account=account,
                date=date,
                meal_type=meal_type,
                time=time,
                kind=kind,
                query=query,
                food_guid=food_guid,
                amount=amount,
                unit=unit,
                unit_guid=unit_guid,
                commit=commit,
            )
            client = registry.get_write_client(request.account)
            resolved = await client.resolve_food(
                query=request.query,
                food_guid=request.food_guid,
                kind=request.kind,
                amount=request.amount,
                unit=request.unit,
                unit_guid=request.unit_guid,
                target_date=request.date,
                target_time=request.time,
                meal_type=request.meal_type,
            )
            preview = _preview(client.alias, resolved, request.commit)
            if not request.commit:
                return preview
            written = await client.record_resolved_food(resolved)
            return {**preview, "status": "written", "written_record": written}
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def record_food_batch(
        account: str,
        date: str,
        items: list[dict[str, Any]],
        default_meal_type: str | None = None,
        default_time: str | None = None,
        commit: bool = False,
        continue_on_error: bool = True,
    ) -> dict[str, Any]:
        """Preview or record normalized food items from assistant/Mealie workflows."""
        try:
            request = RecordFoodBatchRequest(
                account=account,
                date=date,
                items=items,
                default_meal_type=default_meal_type,
                default_time=default_time,
                commit=commit,
                continue_on_error=continue_on_error,
            )
            client = registry.get_write_client(request.account)
        except (ValidationError, ValueError) as err:
            return _error(err)

        results: list[dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        resolved_items: list[dict[str, Any]] = []
        for index, item in enumerate(request.items):
            try:
                resolved = await client.resolve_food(
                    query=item.effective_query(),
                    food_guid=item.food_guid,
                    kind=item.kind,
                    amount=item.amount,
                    unit=item.unit,
                    unit_guid=item.unit_guid,
                    target_date=request.date,
                    target_time=item.time or request.default_time,
                    meal_type=item.meal_type or request.default_meal_type,
                )
                resolved_items.append(resolved)
                results.append(
                    {
                        "index": index,
                        "status": "preview",
                        "source": item.source,
                        "source_id": item.source_id,
                        "preview": _preview(client.alias, resolved, request.commit),
                    }
                )
                success_count += 1
            except KalorickeTabulkyError as err:
                failure_count += 1
                results.append({"index": index, "status": "error", "error": str(err)})
                if not request.continue_on_error:
                    break

        if not request.commit or failure_count:
            return {
                "account": client.alias,
                "mode": "preview" if not request.commit else "validated_with_errors",
                "commit_required_for_write": True,
                "success_count": success_count,
                "failure_count": failure_count,
                "results": results,
            }

        written_results: list[dict[str, Any]] = []
        write_failures = 0
        for index, resolved in enumerate(resolved_items):
            try:
                written_results.append(
                    {
                        "index": index,
                        "status": "written",
                        "written_record": await client.record_resolved_food(resolved),
                    }
                )
            except KalorickeTabulkyError as err:
                write_failures += 1
                written_results.append({"index": index, "status": "error", "error": str(err)})
                if not request.continue_on_error:
                    break
        return {
            "account": client.alias,
            "mode": "commit",
            "success_count": len(written_results) - write_failures,
            "failure_count": write_failures,
            "results": written_results,
        }

    @mcp.tool()
    async def prepare_recipe_import(
        account: str,
        title: str,
        ingredients: list[dict[str, Any]],
        servings: float | None = None,
        total_weight_g: float | None = None,
        source: str | None = None,
        source_id: str | None = None,
        continue_on_error: bool = True,
    ) -> dict[str, Any]:
        """Resolve recipe ingredients for KT custom recipe creation preview."""
        try:
            request = PrepareRecipeImportRequest(
                account=account,
                title=title,
                ingredients=ingredients,
                servings=servings,
                total_weight_g=total_weight_g,
                source=source,
                source_id=source_id,
                continue_on_error=continue_on_error,
            )
            client = registry.get_write_client(request.account)
        except (ValidationError, ValueError) as err:
            return _error(err)

        results: list[dict[str, Any]] = []
        resolved_ingredients: list[dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        for index, ingredient in enumerate(request.ingredients):
            try:
                resolved = await client.resolve_food(
                    query=ingredient.effective_query(),
                    food_guid=ingredient.food_guid,
                    kind=ingredient.kind,
                    amount=ingredient.amount,
                    unit=ingredient.unit,
                    unit_guid=ingredient.unit_guid,
                    target_date=date.today(),
                )
                preview = _recipe_ingredient_preview(index, ingredient, resolved)
                results.append(preview)
                resolved_ingredients.append(preview)
                success_count += 1
            except KalorickeTabulkyError as err:
                failure_count += 1
                results.append(
                    {
                        "index": index,
                        "status": "error",
                        "source": ingredient.source,
                        "source_id": ingredient.source_id,
                        "error": str(err),
                    }
                )
                if not request.continue_on_error:
                    break

        return {
            "account": client.alias,
            "title": request.title,
            "mode": "preview",
            "write_supported": True,
            "commit_required_for_write": True,
            "recipe_endpoint_status": "verified",
            "next_step": (
                "Call create_custom_recipe with the same ingredients and commit=true "
                "to create the private KT recipe, then use record_recipe_serving "
                "with the returned recipe_guid."
            ),
            "servings": request.servings,
            "total_weight_g": request.total_weight_g,
            "source": request.source,
            "source_id": request.source_id,
            "success_count": success_count,
            "failure_count": failure_count,
            "ingredients": results,
            "resolved_ingredients": resolved_ingredients,
        }

    @mcp.tool()
    async def create_custom_recipe(
        account: str,
        title: str,
        ingredients: list[dict[str, Any]],
        servings: float | None = None,
        recipe_guid: str | None = None,
        preparation_time_minutes: int | None = None,
        visibility: str = "private",
        description: list[str] | None = None,
        total_weight_g: float | None = None,
        source: str | None = None,
        source_id: str | None = None,
        include_payload: bool = False,
        commit: bool = False,
        continue_on_error: bool = True,
    ) -> dict[str, Any]:
        """Preview or create a real KT custom recipe from resolved ingredients.

        Writes only with commit=true. The returned recipe_guid can be used by
        record_recipe_serving to log portions of the created recipe.
        """
        try:
            request = CreateCustomRecipeRequest(
                account=account,
                title=title,
                ingredients=ingredients,
                servings=servings,
                recipe_guid=recipe_guid,
                preparation_time_minutes=preparation_time_minutes,
                visibility=visibility,  # type: ignore[arg-type]
                description=description,
                total_weight_g=total_weight_g,
                source=source,
                source_id=source_id,
                include_payload=include_payload,
                commit=commit,
                continue_on_error=continue_on_error,
            )
            client = registry.get_write_client(request.account)
        except (ValidationError, ValueError) as err:
            return _error(err)

        results: list[dict[str, Any]] = []
        resolved_ingredients: list[dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        for index, ingredient in enumerate(request.ingredients):
            try:
                resolved = await client.resolve_food(
                    query=ingredient.effective_query(),
                    food_guid=ingredient.food_guid,
                    kind=ingredient.kind,
                    amount=ingredient.amount,
                    unit=ingredient.unit,
                    unit_guid=ingredient.unit_guid,
                    target_date=date.today(),
                )
                preview = _recipe_ingredient_preview(index, ingredient, resolved)
                results.append(preview)
                resolved_ingredients.append(resolved)
                success_count += 1
            except KalorickeTabulkyError as err:
                failure_count += 1
                results.append(
                    {
                        "index": index,
                        "status": "error",
                        "source": ingredient.source,
                        "source_id": ingredient.source_id,
                        "error": str(err),
                    }
                )
                if not request.continue_on_error:
                    break

        payload = None
        if not failure_count:
            try:
                payload = client.build_custom_recipe_payload(
                    title=request.title,
                    resolved_ingredients=resolved_ingredients,
                    servings=request.servings,
                    preparation_time_minutes=request.preparation_time_minutes,
                    visibility=request.visibility,
                    description=request.description,
                    recipe_guid=request.recipe_guid or "0",
                )
            except KalorickeTabulkyError as err:
                return _error(err)

        preview = {
            "account": client.alias,
            "title": request.title,
            "mode": "commit" if request.commit else "preview",
            "commit_required_for_write": True,
            "write_supported": True,
            "recipe_guid": request.recipe_guid,
            "recipe_endpoint": (
                f"/user/settings/meal/detail/edit/{request.recipe_guid or '0'}?format=json"
            ),
            "servings": request.servings,
            "preparation_time_minutes": request.preparation_time_minutes,
            "visibility": request.visibility,
            "source": request.source,
            "source_id": request.source_id,
            "success_count": success_count,
            "failure_count": failure_count,
            "ingredients": results,
            "payload_summary": _recipe_payload_summary(payload),
        }
        if request.include_payload:
            preview["payload"] = payload
        if not request.commit or failure_count:
            return preview

        try:
            written = await client.create_custom_recipe(
                title=request.title,
                resolved_ingredients=resolved_ingredients,
                servings=request.servings,
                recipe_guid=request.recipe_guid,
                preparation_time_minutes=request.preparation_time_minutes,
                visibility=request.visibility,
                description=request.description,
            )
        except KalorickeTabulkyError as err:
            return _error(err)
        return {
            **preview,
            "status": "written",
            "recipe_guid": written.get("recipe_guid"),
            "written_record": written,
            "next_step": "Use record_recipe_serving with recipe_guid to log a portion.",
        }

    @mcp.tool()
    async def upload_custom_recipe_image(
        account: str,
        recipe_guid: str,
        image_base64: str,
        filename: str = "recipe-image.jpg",
        content_type: str = "image/jpeg",
        field_name: str = "file",
        commit: bool = False,
    ) -> dict[str, Any]:
        """Preview or upload an image to an existing KT custom recipe.

        Writes only with commit=true. image_base64 may be raw base64 or a
        data:image/...;base64 URL. The default multipart field name is "file",
        matching the observed KT image/create flow unless a future capture shows
        another form field.
        """
        try:
            request = UploadCustomRecipeImageRequest(
                account=account,
                recipe_guid=recipe_guid,
                image_base64=image_base64,
                filename=filename,
                content_type=content_type,
                field_name=field_name,
                commit=commit,
            )
            image_bytes = _decode_base64_image(request.image_base64)
            client = registry.get_write_client(request.account)
        except (ValidationError, ValueError) as err:
            return _error(err)

        preview = {
            "account": client.alias,
            "mode": "commit" if request.commit else "preview",
            "commit_required_for_write": True,
            "write_supported": True,
            "recipe_guid": request.recipe_guid,
            "image_endpoint": (
                f"/user/settings/meal/detail/{request.recipe_guid}/image/create?format=json"
            ),
            "filename": request.filename,
            "content_type": request.content_type,
            "field_name": request.field_name,
            "size_bytes": len(image_bytes),
        }
        if not request.commit:
            return preview

        try:
            written = await client.upload_custom_recipe_image(
                recipe_guid=request.recipe_guid,
                image_bytes=image_bytes,
                filename=request.filename,
                content_type=request.content_type,
                field_name=request.field_name,
            )
        except KalorickeTabulkyError as err:
            return _error(err)
        return {**preview, "status": "written", "written_record": written}

    @mcp.tool()
    async def record_recipe_serving(
        account: str,
        date: str,
        meal_type: str | None = None,
        time: str | None = None,
        query: str | None = None,
        recipe_guid: str | None = None,
        food_guid: str | None = None,
        amount: float | None = None,
        unit: str | None = None,
        unit_guid: str | None = None,
        commit: bool = False,
    ) -> dict[str, Any]:
        """Preview or record a serving of a KT custom recipe once it exists.

        KT custom recipes appear in the same food/meal autocomplete surface, so
        this intentionally reuses the existing diary add flow.
        """
        try:
            request = RecordRecipeServingRequest(
                account=account,
                date=date,
                meal_type=meal_type,
                time=time,
                query=query,
                recipe_guid=recipe_guid,
                food_guid=food_guid,
                amount=amount,
                unit=unit,
                unit_guid=unit_guid,
                commit=commit,
            )
            client = registry.get_write_client(request.account)
            resolved = await client.resolve_recipe_serving(
                query=request.query,
                recipe_guid=request.recipe_guid or request.food_guid,
                amount=request.amount,
                unit=request.unit,
                unit_guid=request.unit_guid,
                target_date=request.date,
                target_time=request.time,
                meal_type=request.meal_type,
            )
            preview = _preview(client.alias, resolved, request.commit)
            if not request.commit:
                return preview
            written = await client.record_resolved_recipe(resolved)
            return {**preview, "status": "written", "written_record": written}
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def get_diary_summary(account: str | None = None, date: str | None = None) -> dict[str, Any]:
        """Return safe daily diary summary metrics."""
        try:
            request = DiarySummaryRequest(account=account, date=date)
            client = registry.get_read_client(request.account)
            summary = await client.get_summary(request.date)
            return {"account": client.alias, "date": request.date.isoformat(), **summary.as_safe_dict()}
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def get_diary_detail(account: str | None = None, date: str | None = None) -> dict[str, Any]:
        """Return the raw daily diary detail payload for diagnostics."""
        try:
            request = DiarySummaryRequest(account=account, date=date)
            client = registry.get_read_client(request.account)
            detail = await client.get_diary_detail(request.date)
            return {"account": client.alias, **detail}
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def record_weight(
        account: str,
        weight_kg: float,
        date: str | None = None,
        commit: bool = False,
    ) -> dict[str, Any]:
        """Preview or record body weight. Writes only with commit=true."""
        try:
            request = RecordWeightRequest(
                account=account, weight_kg=weight_kg, date=date, commit=commit
            )
            client = registry.get_write_client(request.account)
            preview = {
                "account": client.alias,
                "date": request.date.isoformat(),
                "weight_kg": request.weight_kg,
                "mode": "commit" if request.commit else "preview",
                "commit_required_for_write": True,
            }
            if not request.commit:
                return preview
            return {
                **preview,
                "status": "written",
                "written_record": await client.record_weight(request.weight_kg, request.date),
            }
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)


def _preview(account: str, resolved: dict[str, Any], commit: bool) -> dict[str, Any]:
    return {
        "account": account,
        "mode": "commit" if commit else "preview",
        "commit_required_for_write": True,
        "food_guid": resolved.get("food_guid"),
        "title": resolved.get("title"),
        "date": resolved.get("date"),
        "time": resolved.get("time"),
        "meal_type": resolved.get("meal_type"),
        "unit_guid": resolved.get("unit_guid"),
        "multiplier": resolved.get("multiplier"),
        "search_result": resolved.get("search_result"),
    }


def _recipe_ingredient_preview(index: int, ingredient: Any, resolved: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": index,
        "status": "resolved",
        "source": ingredient.source,
        "source_id": ingredient.source_id,
        "requested": {
            "name": ingredient.effective_query(),
            "amount": ingredient.amount,
            "unit": ingredient.unit,
            "unit_guid": ingredient.unit_guid,
            "kind": ingredient.kind,
            "note": ingredient.note,
        },
        "kt": {
            "food_guid": resolved.get("food_guid"),
            "title": resolved.get("title"),
            "unit_guid": resolved.get("unit_guid"),
            "multiplier": resolved.get("multiplier"),
            "search_result": resolved.get("search_result"),
            "payload": resolved.get("payload"),
        },
    }


def _recipe_payload_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return {
        "title": payload.get("title"),
        "guid": payload.get("guid"),
        "portions": payload.get("portions"),
        "preparationTime": payload.get("preparationTime"),
        "visibility": payload.get("visibility"),
        "ingredient_count": len(items),
        "ingredients": [
            {
                "guid": item.get("guid"),
                "title": item.get("title"),
                "unitCount": item.get("unitCount"),
                "selectedUnitGuid": item.get("selectedUnitGuid"),
            }
            for item in items
            if isinstance(item, dict)
        ],
    }


def _decode_base64_image(value: str) -> bytes:
    text = value.strip()
    if "," in text and text[:64].lower().startswith("data:image/"):
        text = text.split(",", 1)[1]
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ValueError("image_base64 must be valid base64 image data") from err
    if not decoded:
        raise ValueError("image_base64 decoded to empty data")
    if len(decoded) > 5 * 1024 * 1024:
        raise ValueError("image_base64 is too large; max decoded image size is 5 MB")
    return decoded


def _error(err: Exception) -> dict[str, Any]:
    logger.warning("Tool call failed: %s", err)
    return {"status": "error", "error": str(err), "type": err.__class__.__name__}
