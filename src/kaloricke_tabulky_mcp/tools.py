"""MCP tool registration and tool implementation."""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from .account_registry import AccountRegistry
from .client import AmbiguousFoodError, KalorickeTabulkyError
from .clock import local_today
from .concurrency import resolve_food_items
from .errors import short_error
from .models import (
    CreateCustomRecipeRequest,
    DiarySummaryRequest,
    DiaryRangeRequest,
    DiaryEntriesRequest,
    DeleteDiaryEntryRequest,
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
    """Register public tools and opt-in diagnostics."""
    diagnostic = (
        mcp.tool if registry.settings.enable_diagnostics else lambda: lambda func: func
    )

    @mcp.tool()
    async def health_check() -> dict[str, Any]:
        """Check process/config liveness without logging in to KT. No parameters.
        Never writes; this does not validate remote account authentication.
        """
        return {
            "status": "healthy",
            "server": "Kaloricke Tabulky MCP",
            **registry.safe_health(),
        }

    @mcp.tool()
    async def list_accounts() -> dict[str, Any]:
        """Discover configured account aliases and masked email addresses before
        selecting an account. No parameters. Never writes or exposes
        credentials.
        """
        return {"accounts": registry.list_accounts()}

    @mcp.tool()
    async def check_account_auth(account: str) -> dict[str, Any]:
        """Explicitly validate remote login for the required account alias. Makes
        a bounded authentication request and returns safe diagnostics; does
        not write diary data.
        """
        return await registry.check_auth(account)

    @diagnostic()
    async def probe_user_meal_endpoints(account: str) -> dict[str, Any]:
        """Inspect private custom recipe form metadata for the required account.
        Available only with KT_ENABLE_DIAGNOSTICS=true. Reads candidate pages
        and never calls add/save endpoints; use create_custom_recipe with
        account and commit=true to create a recipe.
        """
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
        """Find food/drink GUIDs before previewing diary writes. Requires query;
        account may use KT_DEFAULT_ACCOUNT. kind is food or drink. page starts
        at 0 and limit is 1-50. Never writes. Pass the selected food_guid to
        record_food to avoid ambiguous matches.
        """
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
        """Find private recipe GUIDs for an account before recording a serving.
        Requires account; query is optional, page starts at 0 and limit is
        1-100. Never writes. Use the returned recipe_guid in
        record_recipe_serving.
        """
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
        date: str | None = None,
        meal_type: str | None = None,
        time: str | None = None,
        kind: str = "food",
        query: str | None = None,
        food_guid: str | None = None,
        amount: float | None = None,
        unit: str | None = None,
        unit_guid: str | None = None,
        commit: bool = False,
        allow_fuzzy: bool = False,
        allow_duplicates: bool = False,
    ) -> dict[str, Any]:
        """Preview or record one food/drink entry. Requires account and query or
        food_guid. date is YYYY-MM-DD, defaulting to the local date in
        KT_TIMEZONE. meal_type accepts breakfast, morning_snack, lunch,
        afternoon_snack, dinner, second_dinner, or 1-6. An explicit meal wins
        over HH:MM time; if both are omitted the local current time selects
        the meal. Preview reports meal_type_source. Writes require
        commit=true. Ambiguous queries block writes unless allow_fuzzy=true;
        duplicates are skipped unless allow_duplicates=true.
        """
        try:
            request = RecordFoodRequest(
                account=account,
                date=date or local_today(registry.settings.timezone),
                meal_type=meal_type,
                time=time,
                kind=kind,
                query=query,
                food_guid=food_guid,
                amount=amount,
                unit=unit,
                unit_guid=unit_guid,
                commit=commit,
                allow_fuzzy=allow_fuzzy,
                allow_duplicates=allow_duplicates,
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
                allow_fuzzy=request.allow_fuzzy,
            )
            preview = _preview(client.alias, resolved, request.commit)
            if not request.commit:
                return preview
            written = await client.record_resolved_food(
                resolved, allow_duplicates=request.allow_duplicates
            )
            return _write_result(preview, written)
        except AmbiguousFoodError as err:
            return {
                **_error(err, preview=not commit),
                "account": account,
                "mode": "commit" if commit else "preview",
            }
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
        allow_fuzzy: bool = False,
        allow_duplicates: bool = False,
    ) -> dict[str, Any]:
        """Preview or record multiple normalized food/drink entries. Requires
        account, YYYY-MM-DD date and items. Each item requires a reference and
        positive amount. meal_type accepts breakfast, morning_snack, lunch,
        afternoon_snack, dinner, second_dinner, or 1-6. Item meal/time values
        override default_meal_type/default_time (HH:MM); omitted values use
        local current time. Reads resolve concurrently; writes remain
        sequential and require commit=true. Any resolution error prevents all
        writes; continue_on_error=false stops reported results at the first
        error and stops later writes after a write error. Already-started read
        lookups may finish. allow_fuzzy opts into approximate matches;
        allow_duplicates opts into repeated portions.
        """
        try:
            request = RecordFoodBatchRequest(
                account=account,
                date=date or local_today(registry.settings.timezone),
                items=items,
                default_meal_type=default_meal_type,
                default_time=default_time,
                commit=commit,
                allow_fuzzy=allow_fuzzy,
                allow_duplicates=allow_duplicates,
                continue_on_error=continue_on_error,
            )
            client = registry.get_write_client(request.account)
        except (ValidationError, ValueError) as err:
            return _error(err)

        results: list[dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        resolved_items: list[dict[str, Any]] = []
        resolutions = await resolve_food_items(
            client,
            request.items,
            request.date,
            allow_fuzzy=request.allow_fuzzy,
            meal_type=request.default_meal_type,
            target_time=request.default_time,
        )
        for index, (item, resolved) in enumerate(zip(request.items, resolutions)):
            try:
                if isinstance(resolved, Exception):
                    raise resolved
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
            except (KalorickeTabulkyError, ValueError) as err:
                failure_count += 1
                results.append(
                    {"index": index, **_error(err, preview=not request.commit)}
                )
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
        async with client.diary_write_lock:
            try:
                entries = (
                    []
                    if request.allow_duplicates
                    else await client.get_duplicate_entries(request.date)
                )
            except (KalorickeTabulkyError, ValueError) as err:
                return {"account": client.alias, "mode": "commit", **_error(err)}
            for index, resolved in enumerate(resolved_items):
                try:
                    written_results.append(
                        {
                            "index": index,
                            **_write_result(
                                {},
                                await client.record_resolved_food(
                                    resolved,
                                    allow_duplicates=request.allow_duplicates,
                                    diary_entries=entries,
                                ),
                            ),
                        }
                    )
                except (KalorickeTabulkyError, ValueError) as err:
                    write_failures += 1
                    written_results.append({"index": index, **_error(err)})
                    if not request.continue_on_error:
                        break
        return {
            "account": client.alias,
            "mode": "commit",
            "success_count": len(written_results) - write_failures,
            "failure_count": write_failures,
            "written_count": sum(
                item["status"] == "written" for item in written_results
            ),
            "skipped_duplicate_count": sum(
                item["status"] == "skipped_duplicate" for item in written_results
            ),
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
        allow_fuzzy: bool = False,
    ) -> dict[str, Any]:
        """Resolve ingredients and preview recipe matches without creating a
        recipe. Requires account, title and ingredients (reference plus
        positive amount per ingredient). Uses the local current date in
        KT_TIMEZONE for ingredient forms. Never writes; review matches before
        calling create_custom_recipe with commit=true. allow_fuzzy opts into
        approximate matches, globally or per ingredient. Lookup results retain
        input order.
        """
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
                allow_fuzzy=allow_fuzzy,
            )
            client = registry.get_write_client(request.account)
        except (ValidationError, ValueError) as err:
            return _error(err)

        results: list[dict[str, Any]] = []
        resolved_ingredients: list[dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        resolutions = await resolve_food_items(
            client,
            request.ingredients,
            local_today(registry.settings.timezone),
            allow_fuzzy=request.allow_fuzzy,
        )
        for index, (ingredient, resolved) in enumerate(
            zip(request.ingredients, resolutions)
        ):
            try:
                if isinstance(resolved, Exception):
                    raise resolved
                preview = _recipe_ingredient_preview(index, ingredient, resolved)
                results.append(preview)
                resolved_ingredients.append(preview)
                success_count += 1
            except (KalorickeTabulkyError, ValueError) as err:
                failure_count += 1
                results.append(
                    {
                        "index": index,
                        "status": "error",
                        "source": ingredient.source,
                        "source_id": ingredient.source_id,
                        **_error(err, preview=True),
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
        allow_fuzzy: bool = False,
    ) -> dict[str, Any]:
        """Preview or create/update a private KT recipe from ingredients.
        Requires account, title and ingredients; recipe_guid selects an
        existing recipe to update. Ingredient forms use the local date in
        KT_TIMEZONE. Writes require commit=true and all ingredients to resolve
        successfully. allow_fuzzy opts into approximate matches. The returned
        recipe_guid can be used in record_recipe_serving. include_payload=true
        adds the larger diagnostic payload.
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
                allow_fuzzy=allow_fuzzy,
            )
            client = registry.get_write_client(request.account)
        except (ValidationError, ValueError) as err:
            return _error(err)

        results: list[dict[str, Any]] = []
        resolved_ingredients: list[dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        resolutions = await resolve_food_items(
            client,
            request.ingredients,
            local_today(registry.settings.timezone),
            allow_fuzzy=request.allow_fuzzy,
        )
        for index, (ingredient, resolved) in enumerate(
            zip(request.ingredients, resolutions)
        ):
            try:
                if isinstance(resolved, Exception):
                    raise resolved
                preview = _recipe_ingredient_preview(index, ingredient, resolved)
                results.append(preview)
                resolved_ingredients.append(resolved)
                success_count += 1
            except (KalorickeTabulkyError, ValueError) as err:
                failure_count += 1
                results.append(
                    {
                        "index": index,
                        "status": "error",
                        "source": ingredient.source,
                        "source_id": ingredient.source_id,
                        **_error(err, preview=not request.commit),
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
            except (KalorickeTabulkyError, ValueError) as err:
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
        """Preview or upload an image to an existing recipe. Requires account,
        recipe_guid and image_base64 (raw base64 or data:image/...;base64
        URL). Writes require commit=true. The authenticated account session is
        used; do not provide browser cookies. field_name defaults to file,
        matching the observed KT multipart flow.
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
        date: str | None = None,
        meal_type: str | None = None,
        time: str | None = None,
        query: str | None = None,
        recipe_guid: str | None = None,
        food_guid: str | None = None,
        amount: float | None = None,
        unit: str | None = None,
        unit_guid: str | None = None,
        commit: bool = False,
        allow_fuzzy: bool = False,
        allow_duplicates: bool = False,
    ) -> dict[str, Any]:
        """Preview or record a serving of an existing private recipe. Requires
        account and recipe_guid, food_guid or query. date is YYYY-MM-DD and
        defaults to the local date in KT_TIMEZONE. meal_type accepts
        breakfast, morning_snack, lunch, afternoon_snack, dinner,
        second_dinner, or 1-6. Explicit meal wins over HH:MM time; otherwise
        use local current time. Writes require commit=true; ambiguous queries
        and duplicate portions are blocked/skipped by default. allow_fuzzy and
        allow_duplicates opt into those behaviors.
        """
        try:
            request = RecordRecipeServingRequest(
                account=account,
                date=date or local_today(registry.settings.timezone),
                meal_type=meal_type,
                time=time,
                query=query,
                recipe_guid=recipe_guid,
                food_guid=food_guid,
                amount=amount,
                unit=unit,
                unit_guid=unit_guid,
                commit=commit,
                allow_fuzzy=allow_fuzzy,
                allow_duplicates=allow_duplicates,
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
                allow_fuzzy=request.allow_fuzzy,
            )
            preview = _preview(client.alias, resolved, request.commit)
            if not request.commit:
                return preview
            written = await client.record_resolved_recipe(
                resolved, allow_duplicates=request.allow_duplicates
            )
            return _write_result(preview, written)
        except AmbiguousFoodError as err:
            return {
                **_error(err, preview=not commit),
                "account": account,
                "mode": "commit" if commit else "preview",
            }
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def get_diary_range(account: str, date_from: str, date_to: str) -> dict[str, Any]:
        """Analyze daily nutrient trends over an inclusive range. Requires
        account, date_from and date_to in YYYY-MM-DD format, with at most 92
        days. Returns standard nutrient units, optional energy target/weight
        and averages excluding empty days and missing metrics. Uses
        KT_MAX_CONCURRENCY for bounded reads. Never writes; a failed read is
        an error, not an empty day.
        """
        try:
            request = DiaryRangeRequest(account=account, date_from=date_from, date_to=date_to)
            client = registry.get_write_client(request.account)
            return {"account": client.alias, **await client.get_diary_range(request.date_from, request.date_to)}
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def get_diary_entries(account: str, date: str | None = None) -> dict[str, Any]:
        """Read a compact diary grouped by meal, with per-meal and daily nutrient
        totals. Requires account; date uses YYYY-MM-DD and defaults to the
        local date in KT_TIMEZONE. Missing nutrient totals remain null. Never
        writes. This draft adapter still needs an anonymized live KT fixture
        to verify field mappings.
        """
        try:
            request = DiaryEntriesRequest(account=account, date=date or local_today(registry.settings.timezone))
            client = registry.get_write_client(request.account)
            return {"account": client.alias, **await client.get_diary_entries(request.date)}
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def delete_diary_entry(account: str, date: str, entry_guid: str, commit: bool = False) -> dict[str, Any]:
        """Preview deletion of a diary entry and confirm its membership in the
        requested day. Requires account, YYYY-MM-DD date and entry_guid from
        get_diary_entries. Preview includes title, quantity and meal. Writes
        would require commit=true, but actual deletion is currently disabled
        until its KT method, endpoint and body are verified. commit=true
        currently returns an error without deleting.
        """
        try:
            request = DeleteDiaryEntryRequest(account=account, date=date, entry_guid=entry_guid, commit=commit)
            client = registry.get_write_client(request.account)
            return {"account": client.alias, **await client.delete_diary_entry(request.date, request.entry_guid, commit=request.commit)}
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @mcp.tool()
    async def get_diary_summary(account: str | None = None, date: str | None = None) -> dict[str, Any]:
        """Read daily nutrition and weight metrics. account may use
        KT_DEFAULT_ACCOUNT; date uses YYYY-MM-DD and defaults to the local
        date in KT_TIMEZONE. Never writes. Prefer get_diary_range for trends
        across several days.
        """
        try:
            request = DiarySummaryRequest(
                account=account, date=date or local_today(registry.settings.timezone)
            )
            client = registry.get_read_client(request.account)
            summary = await client.get_summary(request.date)
            return {"account": client.alias, "date": request.date.isoformat(), **summary.as_safe_dict()}
        except (ValidationError, ValueError, KalorickeTabulkyError) as err:
            return _error(err)

    @diagnostic()
    async def get_diary_detail(account: str | None = None, date: str | None = None) -> dict[str, Any]:
        """Diagnose unsupported diary structures using the full raw daily
        payload, which is expensive in tokens. Requires
        KT_ENABLE_DIAGNOSTICS=true. account may use KT_DEFAULT_ACCOUNT; date
        uses YYYY-MM-DD and defaults to the local date in KT_TIMEZONE. Never
        writes. Prefer get_diary_entries for a compact view.
        """
        try:
            request = DiarySummaryRequest(
                account=account, date=date or local_today(registry.settings.timezone)
            )
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
        """Preview or record body weight. Requires account and positive
        weight_kg. date uses YYYY-MM-DD and defaults to the local date in
        KT_TIMEZONE. Writes require commit=true; preview is the default.
        """
        try:
            request = RecordWeightRequest(
                account=account,
                weight_kg=weight_kg,
                date=date or local_today(registry.settings.timezone),
                commit=commit,
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
        "meal_type_source": resolved.get("meal_type_source"),
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


def _error(err: Exception, *, preview: bool = False) -> dict[str, Any]:
    """Error payload; an ambiguous lookup in a preview reports status "ambiguous"."""
    message = short_error(str(err))
    logger.warning("Tool call failed: %s", message)
    result = {"status": "error", "error": message, "type": err.__class__.__name__}
    if isinstance(err, AmbiguousFoodError):
        result.update(ambiguous=True, candidates=err.candidates)
        if preview:
            result["status"] = "ambiguous"
    return result


def _write_result(preview: dict[str, Any], written: dict[str, Any]) -> dict[str, Any]:
    if written.get("status") == "skipped_duplicate":
        return {**preview, **written}
    return {**preview, "status": "written", "written_record": written}
