"""Async client for unofficial Kaloricke Tabulky web endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import md5
from html import unescape
from http.cookies import SimpleCookie
from math import isfinite
import re
from typing import Any
from urllib.parse import urlencode, urljoin

from aiohttp import ClientResponse, ClientSession, ClientTimeout

LOGIN_URL = "https://www.kaloricketabulky.cz/login/create?format=json"
SUMMARY_URL = "https://www.kaloricketabulky.cz/statistic/summary/{date}/get?format=json"
DIARY_SUMMARY_URL = "https://www.kaloricketabulky.cz/user/diary/summary/{date}/get?format=json"
DIARY_DETAIL_URL = "https://www.kaloricketabulky.cz/user/diary/{date}/get?format=json"
RECORD_WEIGHT_URL = "https://www.kaloricketabulky.cz/user/weight/add?format=json&="
SEARCH_FOOD_URL = "https://www.kaloricketabulky.cz/autocomplete/{kind}?{query}"
FOOD_FORM_URL = (
    "https://www.kaloricketabulky.cz/user/foodstuff/add/form/{guid}/{date}/get?format=json"
)
RECORD_FOOD_URL = "https://www.kaloricketabulky.cz/user/foodstuff/add?format=json&="
BASE_URL = "https://www.kaloricketabulky.cz/"
USER_MEAL_PROBE_URLS = (
    "https://www.kaloricketabulky.cz/user/recipe/add",
    "https://www.kaloricketabulky.cz/user/recipe/create",
    "https://www.kaloricketabulky.cz/user/foodstuff-meal/create",
    "https://www.kaloricketabulky.cz/user/settings/meal",
    "https://www.kaloricketabulky.cz/user/settings/meal/detail/0",
)

SEARCH_KINDS = {"food": "foodstuff-meal", "drink": "drink"}

MEAL_TYPES = {
    "1": "1",
    "breakfast": "1",
    "snidane": "1",
    "snídaně": "1",
    "2": "2",
    "morning_snack": "2",
    "brunch": "2",
    "dopoledni_svacina": "2",
    "dopolední_svačina": "2",
    "3": "3",
    "lunch": "3",
    "obed": "3",
    "oběd": "3",
    "4": "4",
    "afternoon_snack": "4",
    "snack": "4",
    "odpoledni_svacina": "4",
    "odpolední_svačina": "4",
    "5": "5",
    "dinner": "5",
    "vecere": "5",
    "večeře": "5",
    "6": "6",
    "second_dinner": "6",
    "druha_vecere": "6",
    "druhá_večeře": "6",
}

DETAIL_NUTRIENT_FIELDS: dict[str, tuple[str, str | None]] = {
    "protein": ("Protein", "g"),
    "carbohydrate": ("Carbohydrates", "g"),
    "fat": ("Fat", "g"),
    "fiber": ("Fiber", "g"),
    "sugar": ("Sugar", "g"),
    "salt": ("Salt", "g"),
}

KNOWN_METRICS: dict[str, tuple[str, str | None]] = {
    "energy": ("Energy", "kcal"),
    "energy_kcal": ("Energy", "kcal"),
    "energykcal": ("Energy", "kcal"),
    "energie": ("Energy", "kcal"),
    "energeticka_hodnota": ("Energy", "kcal"),
    "calorie": ("Energy", "kcal"),
    "calories": ("Energy", "kcal"),
    "kcal": ("Energy", "kcal"),
    "kj": ("Energy", "kJ"),
    "total": ("Energy", "kcal"),
    "activity_energy_total": ("Activity energy", "kcal"),
    "activity_level_energy": ("Activity level energy", "kcal"),
    "basal_metabolism": ("Basal metabolism", "kcal"),
    "cilova_hmotnost": ("Body weight", "kg"),
    "energy_deficit": ("Energy deficit", "kcal"),
    "energy_intake_maintenance": ("Maintenance intake", "kcal"),
    "energy_intake_rest": ("Energy remaining", "kcal"),
    "energy_output_total": ("Energy output total", "kcal"),
    "energy_target": ("Energy target", "kcal"),
    "protein": ("Protein", "g"),
    "proteins": ("Protein", "g"),
    "bilkoviny": ("Protein", "g"),
    "carbohydrate": ("Carbohydrates", "g"),
    "carbohydrates": ("Carbohydrates", "g"),
    "carbs": ("Carbohydrates", "g"),
    "sacharidy": ("Carbohydrates", "g"),
    "fat": ("Fat", "g"),
    "fats": ("Fat", "g"),
    "mastne_kyseliny_nasycene": ("Saturated fat", "g"),
    "nasycene_mastne_kyseliny": ("Saturated fat", "g"),
    "tuky": ("Fat", "g"),
    "fiber": ("Fiber", "g"),
    "fibre": ("Fiber", "g"),
    "vlaknina": ("Fiber", "g"),
    "sugar": ("Sugar", "g"),
    "sugars": ("Sugar", "g"),
    "cukry": ("Sugar", "g"),
    "z_toho_cukry": ("Sugar", "g"),
    "salt": ("Salt", "g"),
    "sul": ("Salt", "g"),
    "water": ("Water", "ml"),
    "pitny_rezim": ("Water", "ml"),
    "voda": ("Water", "ml"),
    "weight": ("Body weight", "kg"),
}

IGNORED_SUMMARY_KEYS = {
    "code",
    "date",
    "description",
    "id",
    "monthweight",
    "name",
    "slug",
    "timestamp",
    "title",
    "unit",
    "value",
}


class KalorickeTabulkyError(Exception):
    """Base error for Kaloricke Tabulky API failures."""


class InvalidAuthError(KalorickeTabulkyError):
    """Raised when credentials are rejected."""


class SessionExpiredError(KalorickeTabulkyError):
    """Raised when the API session is no longer authenticated."""


@dataclass(slots=True, frozen=True)
class AccountCredentials:
    """Credentials for one configured account."""

    alias: str
    email: str
    password: str

    def safe_dict(self) -> dict[str, str]:
        return {"alias": self.alias, "email": _mask_email(self.email)}


@dataclass(slots=True, frozen=True)
class WeightRecord:
    """A weight record returned by Kaloricke Tabulky."""

    date_label: str
    weight: float


@dataclass(slots=True, frozen=True)
class SummaryMetric:
    """A numeric metric returned by the summary endpoint."""

    key: str
    name: str
    value: float
    unit: str | None = None
    goal: float | None = None
    percent: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "goal": self.goal,
            "percent": self.percent,
        }


@dataclass(slots=True, frozen=True)
class SummaryData:
    """Parsed summary data returned by the summary endpoints."""

    weight_records: list[WeightRecord]
    metrics: dict[str, SummaryMetric]
    raw: dict[str, Any]

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "weight_records": [
                {"date_label": item.date_label, "weight": item.weight}
                for item in self.weight_records
            ],
            "metrics": {key: metric.as_dict() for key, metric in self.metrics.items()},
        }


class KalorickeTabulkyClient:
    """Small async client for the unofficial Kaloricke Tabulky endpoints."""

    def __init__(
        self,
        credentials: AccountCredentials,
        *,
        request_timeout: int = 30,
        session: ClientSession | None = None,
    ) -> None:
        self._credentials = credentials
        self._owns_session = session is None
        self._session = session or ClientSession(timeout=ClientTimeout(total=request_timeout))
        self._cookies: str | None = None

    @property
    def alias(self) -> str:
        return self._credentials.alias

    @property
    def closed(self) -> bool:
        return self._session.closed

    async def close(self) -> None:
        if self._owns_session and not self._session.closed:
            await self._session.close()

    async def authenticate(self) -> None:
        response = await self._session.post(
            LOGIN_URL,
            json={
                "email": self._credentials.email,
                "password": md5(
                    self._credentials.password.encode(), usedforsecurity=False
                ).hexdigest(),
            },
        )
        body = await self._json_response(response)
        if body.get("code") != 0:
            raise InvalidAuthError(body.get("message") or "Invalid credentials")

        cookies = SimpleCookie()
        for header in response.headers.getall("Set-Cookie", []):
            cookies.load(header)

        cookie_header = "; ".join(f"{key}={morsel.value}" for key, morsel in cookies.items())
        if not cookie_header:
            raise InvalidAuthError("Authentication did not return session cookies")

        self._cookies = cookie_header

    async def auth_check(self) -> dict[str, Any]:
        await self.authenticate()
        return {"account": self.alias, "status": "ok"}

    async def get_summary(self, target_date: date | None = None) -> SummaryData:
        request_date = target_date or date.today()
        statistic_body = await self._request_with_reauth(
            "GET",
            SUMMARY_URL.format(date=self._format_date(request_date)),
        )
        statistic_data = statistic_body.get("data") or {}
        month_weight = statistic_data.get("monthWeight") or []
        weight_records = [
            WeightRecord(date_label=str(item["description"]), weight=float(item["value"]))
            for item in month_weight
            if item.get("value") is not None
        ]

        diary_body = await self._request_with_reauth(
            "GET",
            DIARY_SUMMARY_URL.format(date=self._format_date(request_date)),
        )
        diary_data = diary_body.get("data") or {}
        metrics = _extract_diary_summary_metrics(diary_data)
        try:
            detail_body = await self._request_with_reauth(
                "GET",
                DIARY_DETAIL_URL.format(date=self._format_date(request_date)),
            )
            detail_data = detail_body.get("data") or {}
        except KalorickeTabulkyError:
            detail_data = {}
        _add_detail_fallback_metrics(detail_data, metrics)

        return SummaryData(
            weight_records=weight_records,
            metrics=metrics,
            raw={"statistic": statistic_data, "diary": diary_data, "detail": detail_data},
        )

    async def get_diary_detail(self, target_date: date) -> dict[str, Any]:
        body = await self._request_with_reauth(
            "GET", DIARY_DETAIL_URL.format(date=self._format_date(target_date))
        )
        return {"date": target_date.isoformat(), "data": body.get("data") or {}}

    async def record_weight(self, weight: float, target_date: date) -> dict[str, Any]:
        return await self._request_with_reauth(
            "POST",
            RECORD_WEIGHT_URL,
            json={"weight": weight, "date": self._format_date(target_date)},
        )

    async def search_food(
        self, query: str, kind: str = "food", page: int = 0, limit: int | None = None
    ) -> list[dict[str, Any]]:
        search_kind = _search_kind(kind)
        query_string = urlencode({"query": query, "page": page, "format": "json"})
        body = await self._request_any_with_reauth(
            "GET", SEARCH_FOOD_URL.format(kind=search_kind, query=query_string)
        )
        if not isinstance(body, list):
            raise KalorickeTabulkyError(f"Unexpected search response: {body}")
        results = [_normalize_search_result(item) for item in body if isinstance(item, dict)]
        return results[:limit] if limit is not None else results

    async def resolve_food(
        self,
        *,
        query: str | None = None,
        food_guid: str | None = None,
        kind: str = "food",
        amount: float | None = None,
        unit: str | None = None,
        unit_guid: str | None = None,
        target_date: date,
        target_time: str | None = None,
        meal_type: str | None = None,
    ) -> dict[str, Any]:
        search_result: dict[str, Any] | None = None
        if food_guid is None:
            if not query:
                raise KalorickeTabulkyError("Set query or food_guid")
            results = await self.search_food(query, kind, limit=10)
            search_result = next(
                (item for item in results if item.get("class") == "foodstuff"),
                next((item for item in results if item.get("food_guid")), None),
            )
            if search_result is None:
                raise KalorickeTabulkyError(f"No food result found for query: {query}")
            food_guid = str(search_result["food_guid"])

        form_body = await self._request_with_reauth(
            "GET", FOOD_FORM_URL.format(guid=food_guid, date=self._format_date(target_date))
        )
        form = form_body.get("data")
        if not isinstance(form, dict):
            raise KalorickeTabulkyError(f"Unexpected add form response: {form_body}")

        payload = dict(form)
        payload["date"] = self._format_date(target_date)
        if target_time is not None:
            payload["timeUser"] = True
            payload["time"] = target_time
        payload["diaryTimeGuid"] = _meal_type_id(meal_type) or _meal_type_from_time(target_time)
        _apply_unit_selection(payload, amount=amount, unit=unit, unit_guid=unit_guid)
        return {
            "food_guid": food_guid,
            "title": payload.get("title"),
            "date": payload.get("date"),
            "time": payload.get("time"),
            "meal_type": payload.get("diaryTimeGuid"),
            "unit_guid": payload.get("unitGuid"),
            "multiplier": payload.get("multiplier"),
            "payload": payload,
            "search_result": search_result,
        }

    async def record_resolved_food(self, resolved: dict[str, Any]) -> dict[str, Any]:
        response = await self._request_with_reauth("POST", RECORD_FOOD_URL, json=resolved["payload"])
        return {
            "message": response.get("message"),
            "food_guid": resolved.get("food_guid"),
            "title": resolved.get("title"),
            "date": resolved.get("date"),
            "time": resolved.get("time"),
            "meal_type": resolved.get("meal_type"),
            "unit_guid": resolved.get("unit_guid"),
            "multiplier": resolved.get("multiplier"),
            "search_result": resolved.get("search_result"),
        }

    async def probe_user_meal_endpoints(self) -> dict[str, Any]:
        """Read authenticated custom meal/recipe pages to discover private form endpoints."""
        probes = []
        discovered: set[str] = set()
        for url in USER_MEAL_PROBE_URLS:
            result = await self._request_text_with_reauth("GET", url)
            text = result.pop("text")
            endpoints = _extract_candidate_endpoints(text)
            discovered.update(endpoints)
            probes.append(
                {
                    **result,
                    "title": _html_title(text),
                    "login_page": "/login" in str(result.get("location") or "")
                    or "Přihlášení" in text[:5000],
                    "forms": _extract_probe_forms(text),
                    "scripts": _extract_probe_scripts(text),
                    "candidate_endpoints": endpoints,
                }
            )
        return {
            "account": self.alias,
            "mode": "read_only_probe",
            "write_performed": False,
            "probes": probes,
            "candidate_endpoints": sorted(discovered),
            "blocked_write_reason": (
                "Custom recipe creation still needs a verified authenticated POST "
                "URL and payload. This tool only reads candidate form pages and "
                "does not call add/save JSON endpoints."
            ),
        }

    async def _request_with_reauth(
        self, method: str, url: str, **kwargs: Any
    ) -> dict[str, Any]:
        if self._cookies is None:
            await self.authenticate()

        try:
            return await self._request(method, url, **kwargs)
        except SessionExpiredError:
            await self.authenticate()
            return await self._request(method, url, **kwargs)

    async def _request_any_with_reauth(
        self, method: str, url: str, **kwargs: Any
    ) -> Any:
        if self._cookies is None:
            await self.authenticate()

        try:
            return await self._request_any(method, url, **kwargs)
        except SessionExpiredError:
            await self.authenticate()
            return await self._request_any(method, url, **kwargs)

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        body = await self._request_any(method, url, **kwargs)
        if not isinstance(body, dict):
            raise KalorickeTabulkyError(f"Unexpected API response: {body}")
        if body.get("code") != 0:
            raise KalorickeTabulkyError(body.get("message") or f"Unexpected API response: {body}")
        return body

    async def _request_any(self, method: str, url: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        headers["Cookie"] = self._cookies or ""
        response = await self._session.request(
            method, url, headers=headers, allow_redirects=False, **kwargs
        )
        body = await self._json_response(response)
        if response.status in (301, 302, 303, 307, 308):
            raise SessionExpiredError("Session expired")
        if isinstance(body, dict) and body.get("code") not in (None, 0):
            raise KalorickeTabulkyError(body.get("message") or f"Unexpected API response: {body}")
        return body

    async def _request_text_with_reauth(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        if self._cookies is None:
            await self.authenticate()

        result = await self._request_text(method, url, **kwargs)
        if result["status"] in (301, 302, 303, 307, 308):
            await self.authenticate()
            result = await self._request_text(method, url, **kwargs)
        return result

    async def _request_text(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers["Cookie"] = self._cookies or ""
        response = await self._session.request(
            method, url, headers=headers, allow_redirects=False, **kwargs
        )
        text = await response.text()
        return {
            "url": url,
            "status": response.status,
            "content_type": response.headers.get("Content-Type", ""),
            "location": response.headers.get("Location"),
            "text": text,
        }

    @staticmethod
    async def _json_response(response: ClientResponse) -> Any:
        content_type = response.headers.get("Content-Type", "")
        if response.status in (301, 302, 303, 307, 308):
            raise SessionExpiredError("Session expired")
        if "json" not in content_type:
            text = await response.text()
            raise KalorickeTabulkyError(f"Unexpected non-JSON response: {text[:200]}")
        return await response.json()

    @staticmethod
    def _format_date(value: date) -> str:
        return value.strftime("%d.%m.%Y")


def parse_public_date(value: str | date | None) -> date:
    if isinstance(value, date):
        return value
    if not value:
        return date.today()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError("Date must use YYYY-MM-DD or DD.MM.YYYY format")


def parse_public_time(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?", text)
    if not match:
        raise ValueError("Time must use HH:MM format")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _extract_summary_metrics(data: dict[str, Any]) -> dict[str, SummaryMetric]:
    metrics: dict[str, SummaryMetric] = {}
    _collect_metric_values(data, (), metrics)
    return metrics


def _extract_diary_summary_metrics(data: dict[str, Any]) -> dict[str, SummaryMetric]:
    metrics: dict[str, SummaryMetric] = {}
    for item in _iter_diary_items(data.get("items")):
        metric = _metric_from_diary_item(item)
        if metric is not None:
            metrics.setdefault(metric.key, metric)

    for item in _iter_diary_items(data.get("itemsDynamic")):
        metric = _metric_from_diary_item(item)
        if metric is not None:
            metrics.setdefault(metric.key, metric)

    _add_diary_total_metrics(data, metrics)
    _add_balance_metrics(data.get("balance"), metrics)
    if not metrics:
        metrics = _extract_summary_metrics(data)
    return metrics


def _iter_diary_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        items.extend(_iter_diary_items(item))
    return items


def _metric_from_diary_item(item: dict[str, Any]) -> SummaryMetric | None:
    value = _diary_item_value(item)
    if value is None:
        return None
    raw_name = item.get("code") or item.get("title") or item.get("titleShort")
    if not raw_name:
        return None
    key = _normalize_key(str(raw_name))
    known = KNOWN_METRICS.get(key)
    name = _clean_name(str(raw_name))
    unit = _clean_unit(item.get("unit"))
    if known is not None:
        name, default_unit = known
        unit = unit or default_unit
    percent = item.get("percent")
    return SummaryMetric(
        key=key,
        name=name,
        value=value,
        unit=unit,
        goal=_parse_localized_number(item.get("goal")),
        percent=percent if isinstance(percent, int) and not isinstance(percent, bool) else None,
    )


def _diary_item_value(item: dict[str, Any]) -> float | None:
    actual_value = _parse_localized_number(item.get("actualValue"))
    actual = _parse_localized_number(item.get("actual"))
    if actual_value not in (None, 0.0):
        return actual_value
    return actual


def _add_diary_total_metrics(data: dict[str, Any], metrics: dict[str, SummaryMetric]) -> None:
    value = _parse_localized_number(data.get("activityEnergyTotal"))
    if value is not None:
        metrics.setdefault("activity_energy_total", _known_metric("activity_energy_total", value))
    weight = _parse_localized_number(data.get("weight"))
    if weight is not None and "cilova_hmotnost" not in metrics:
        metrics.setdefault("weight", _known_metric("weight", weight))


def _add_balance_metrics(value: Any, metrics: dict[str, SummaryMetric]) -> None:
    if not isinstance(value, dict):
        return
    balance_fields = {
        "energyOutputTotal": "energy_output_total",
        "energyIntakeMaintenance": "energy_intake_maintenance",
        "energyDeficit": "energy_deficit",
        "target": "energy_target",
        "basal": "basal_metabolism",
        "intakeRest": "energy_intake_rest",
    }
    for source_key, metric_key in balance_fields.items():
        metric_value = _parse_localized_number(value.get(source_key))
        if metric_value is not None:
            metrics.setdefault(metric_key, _known_metric(metric_key, metric_value))
    aml = value.get("aml")
    if isinstance(aml, dict):
        aml_energy = _parse_localized_number(aml.get("energy"))
        if aml_energy is not None:
            metric = _known_metric("activity_level_energy", aml_energy)
            unit = _clean_unit(aml.get("energyUnit")) or metric.unit
            metrics.setdefault(
                metric.key,
                SummaryMetric(key=metric.key, name=metric.name, value=metric.value, unit=unit),
            )


def _add_detail_fallback_metrics(
    data: dict[str, Any], metrics: dict[str, SummaryMetric]
) -> None:
    totals = {key: 0.0 for key in DETAIL_NUTRIENT_FIELDS}
    found = {key: False for key in DETAIL_NUTRIENT_FIELDS}
    for item in _iter_foodstuff_items(data):
        for key in DETAIL_NUTRIENT_FIELDS:
            value = _parse_localized_number(item.get(key))
            if value is None:
                continue
            totals[key] += value
            found[key] = True
    for key, was_found in found.items():
        if was_found and key not in metrics:
            name, unit = DETAIL_NUTRIENT_FIELDS[key]
            metrics[key] = SummaryMetric(key=key, name=name, value=totals[key], unit=unit)


def _iter_foodstuff_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        items: list[dict[str, Any]] = []
        foodstuff = value.get("foodstuff")
        if isinstance(foodstuff, list):
            items.extend(item for item in foodstuff if isinstance(item, dict))
        for child in value.values():
            if isinstance(child, dict | list):
                items.extend(_iter_foodstuff_items(child))
        return items
    if isinstance(value, list):
        items = []
        for child in value:
            if isinstance(child, dict | list):
                items.extend(_iter_foodstuff_items(child))
        return items
    return []


def _known_metric(key: str, value: float) -> SummaryMetric:
    name, unit = KNOWN_METRICS[key]
    return SummaryMetric(key=key, name=name, value=value, unit=unit)


def _collect_metric_values(
    value: Any, path: tuple[str, ...], metrics: dict[str, SummaryMetric]
) -> None:
    if isinstance(value, dict):
        metric = _metric_from_value_object(value, path)
        if metric is not None:
            metrics.setdefault(metric.key, metric)
        for child_key, child_value in value.items():
            if _normalize_key(child_key) in IGNORED_SUMMARY_KEYS:
                continue
            if _is_number(child_value):
                metric = _metric_from_plain_value(child_key, child_value, path)
                if metric is not None:
                    metrics.setdefault(metric.key, metric)
            elif isinstance(child_value, dict | list):
                _collect_metric_values(child_value, (*path, str(child_key)), metrics)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict):
                metric = _metric_from_value_object(item, path)
                if metric is not None:
                    metrics.setdefault(metric.key, metric)
                else:
                    _collect_metric_values(item, (*path, str(index)), metrics)


def _metric_from_value_object(item: dict[str, Any], path: tuple[str, ...]) -> SummaryMetric | None:
    if not _is_number(item.get("value")):
        return None
    raw_name = (
        item.get("description")
        or item.get("name")
        or item.get("title")
        or item.get("label")
        or (path[-1] if path else None)
    )
    if not raw_name:
        return None
    normalized_name = _normalize_key(str(raw_name))
    normalized_key = (
        normalized_name
        if normalized_name in KNOWN_METRICS
        else _normalize_key(_metric_key(path, str(raw_name)))
    )
    known = KNOWN_METRICS.get(normalized_key)
    unit = _clean_unit(item.get("unit") or item.get("unitName") or item.get("unit_name"))
    name = _clean_name(str(raw_name))
    if known is not None:
        name, default_unit = known
        unit = unit or default_unit
    if known is None and unit is None and normalized_key not in KNOWN_METRICS:
        return None
    return SummaryMetric(key=normalized_key, name=name, value=float(item["value"]), unit=unit)


def _metric_from_plain_value(key: str, value: Any, path: tuple[str, ...]) -> SummaryMetric | None:
    metric_key = _normalize_key(_metric_key(path, key))
    simple_key = _normalize_key(key)
    known = KNOWN_METRICS.get(simple_key) or KNOWN_METRICS.get(metric_key)
    if known is None:
        return None
    return SummaryMetric(
        key=simple_key if simple_key in KNOWN_METRICS else metric_key,
        name=known[0],
        value=float(value),
        unit=known[1],
    )


def _metric_key(path: tuple[str, ...], name: str) -> str:
    return "_".join(part for part in (*path, name) if not part.isdigit())


def _normalize_key(value: str) -> str:
    value = value.lower()
    value = value.translate(str.maketrans("áčďéěíňóřšťúůýž", "acdeeinorstuuyz"))
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def _clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().capitalize()


def _clean_unit(value: Any) -> str | None:
    if value is None:
        return None
    unit = str(value).strip()
    return unit or None


def _parse_localized_number(value: Any) -> float | None:
    if _is_number(value):
        return float(value)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("\xa0", " ").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _search_kind(value: str) -> str:
    try:
        return SEARCH_KINDS[value]
    except KeyError as err:
        raise KalorickeTabulkyError("kind must be food or drink") from err


def _normalize_search_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "food_guid": item.get("id"),
        "title": item.get("title"),
        "class": item.get("clazz"),
        "url": item.get("url"),
        "unit": item.get("unit"),
        "energy": _parse_localized_number(item.get("value")),
        "energy_unit": item.get("energyUnit"),
        "brand_name": item.get("brandName"),
        "favorite": item.get("favorite"),
        "is_liquid": item.get("isLiquid") if "isLiquid" in item else item.get("liquid"),
        "status": item.get("status"),
    }


def _meal_type_id(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().lower().replace(" ", "_")
    if key in MEAL_TYPES:
        return MEAL_TYPES[key]
    raise KalorickeTabulkyError(
        "meal_type must be one of breakfast, morning_snack, lunch, "
        "afternoon_snack, dinner, second_dinner, or 1-6"
    )


def _meal_type_from_time(value: str | None) -> str:
    if value is None:
        return "1"
    hour, minute = (int(part) for part in value.split(":"))
    minutes = hour * 60 + minute
    if 5 * 60 <= minutes < 10 * 60:
        return "1"
    if 10 * 60 <= minutes < 11 * 60 + 30:
        return "2"
    if 11 * 60 + 30 <= minutes < 14 * 60 + 30:
        return "3"
    if 14 * 60 + 30 <= minutes < 17 * 60 + 30:
        return "4"
    if 17 * 60 + 30 <= minutes < 21 * 60 + 30:
        return "5"
    return "6"


def _apply_unit_selection(
    payload: dict[str, Any],
    *,
    amount: float | None,
    unit: str | None,
    unit_guid: str | None,
) -> None:
    if unit_guid:
        payload["unitGuid"] = unit_guid
        if amount is not None:
            _validate_amount(amount)
            payload["multiplier"] = amount
        return
    if amount is None:
        return
    _validate_amount(amount)
    options = [
        option
        for option in payload.get("unitOptions", [])
        if isinstance(option, dict) and option.get("id")
    ]
    selected = _find_unit_option(options, amount, unit)
    if selected is None:
        payload["multiplier"] = amount
        return
    payload["unitGuid"] = selected["id"]
    selected_multiplier = _parse_localized_number(selected.get("multiplier"))
    if selected_multiplier is not None and abs(selected_multiplier - amount) < 0.000001:
        payload["multiplier"] = 1.0
    elif selected_multiplier == 1:
        payload["multiplier"] = amount
    else:
        payload["multiplier"] = amount


def _validate_amount(amount: float) -> None:
    if not isfinite(amount) or amount <= 0:
        raise KalorickeTabulkyError("amount must be a positive finite number")


def _find_unit_option(
    options: list[dict[str, Any]], amount: float, unit: str | None
) -> dict[str, Any] | None:
    normalized_unit = unit.strip().lower() if unit else None
    unit_matches = [
        option
        for option in options
        if normalized_unit is None
        or normalized_unit
        in re.sub(r"[^a-z0-9]+", " ", str(option.get("title", "")).lower()).split()
    ]
    for option in unit_matches:
        multiplier = _parse_localized_number(option.get("multiplier"))
        if multiplier is not None and abs(multiplier - amount) < 0.000001:
            return option
    if normalized_unit is not None:
        for option in unit_matches:
            multiplier = _parse_localized_number(option.get("multiplier"))
            if multiplier == 1:
                return option
    return None


def _extract_candidate_endpoints(text: str) -> list[str]:
    endpoints = set()
    patterns = (
        r"['\"]([^'\"]*(?:user/recipe|user/settings/meal|user/meal|/meal/|/recipe/|foodstuff)[^'\"]*)['\"]",
        r"\b((?:/)?(?:user/recipe|user/settings/meal|user/meal|meal|recipe|foodstuff)[^\\s'\"<>)]*)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            endpoint = unescape(match.group(1).replace("\\/", "/"))
            if len(endpoint) > 240:
                continue
            if any(token in endpoint.lower() for token in ("password", "email", "cookie")):
                continue
            endpoints.add(_redact_query_values(endpoint))
    return sorted(endpoints)


def _extract_probe_forms(text: str) -> list[dict[str, Any]]:
    forms = []
    for match in re.finditer(r"<form\b(?P<attrs>.*?)>", text, re.IGNORECASE | re.DOTALL):
        attrs = _html_attrs(match.group("attrs"))
        action = attrs.get("action")
        forms.append(
            {
                "method": (attrs.get("method") or "get").lower(),
                "action": _redact_query_values(urljoin(BASE_URL, action)) if action else None,
                "input_names": _form_input_names(text, match.end()),
            }
        )
    return forms


def _extract_probe_scripts(text: str) -> list[str]:
    scripts = [
        urljoin(BASE_URL, unescape(src))
        for src in re.findall(r"<script[^>]+src=[\"']([^\"']+)", text, re.IGNORECASE)
    ]
    return _dedupe(scripts)


def _html_attrs(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r"([\w:-]+)\s*=\s*([\"'])(.*?)\2", text, re.DOTALL):
        attrs[match.group(1).lower()] = unescape(match.group(3))
    return attrs


def _form_input_names(text: str, form_start: int) -> list[str]:
    close = text.find("</form>", form_start)
    if close == -1:
        close = min(len(text), form_start + 20000)
    form_html = text[form_start:close]
    names = re.findall(r"<(?:input|select|textarea)\b[^>]*\bname=[\"']([^\"']+)", form_html, re.I)
    return _dedupe(unescape(name) for name in names)


def _html_title(text: str) -> str | None:
    match = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return re.sub(r"\s+", " ", unescape(match.group(1))).strip()[:160]


def _redact_query_values(value: str) -> str:
    return re.sub(r"([?&][^=&]+)=([^&#]*)", r"\1=<redacted>", value)


def _dedupe(values: Any) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _safe_probe_snippet(text: str) -> str:
    compact = re.sub(r"\s+", " ", text)
    focus_terms = (
        "user/settings/meal",
        "foodstuff",
        "ingredient",
        "ingredience",
        "surovin",
        "form",
        "ng-controller",
    )
    snippets = []
    for term in focus_terms:
        index = compact.lower().find(term.lower())
        if index < 0:
            continue
        snippets.append(compact[max(0, index - 220) : index + 500])
        if len(snippets) >= 3:
            break
    snippet = "\n---\n".join(snippets) if snippets else compact[:900]
    return re.sub(
        r"([A-Za-z0-9._%+-])([A-Za-z0-9._%+-]*)(@[A-Za-z0-9.-]+)",
        r"\1***\3",
        snippet,
    )[:3000]


def _mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    name, domain = email.split("@", 1)
    if not name:
        return f"***@{domain}"
    return f"{name[0]}***@{domain}"
