"""Resolve read-only inputs concurrently while retaining deterministic order."""

import asyncio
from datetime import date
from typing import Any

from .client import KalorickeTabulkyError


async def resolve_food_items(
    client: Any,
    items: list[Any],
    target_date: date,
    *,
    meal_type: str | None = None,
    target_time: str | None = None,
    allow_fuzzy: bool = False,
) -> list[dict[str, Any] | Exception]:
    async def resolve(item: Any) -> dict[str, Any] | Exception:
        try:
            # The client's resolve_food holds the same semaphore as range reads.
            return await client.resolve_food(
                query=item.effective_query(),
                food_guid=item.food_guid,
                kind=item.kind,
                amount=item.amount,
                unit=item.unit,
                unit_guid=item.unit_guid,
                target_date=target_date,
                target_time=item.time or target_time,
                meal_type=item.meal_type or meal_type,
                allow_fuzzy=item.allow_fuzzy or allow_fuzzy,
            )
        except (KalorickeTabulkyError, ValueError) as err:
            return err

    tasks = [asyncio.create_task(resolve(item)) for item in items]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
