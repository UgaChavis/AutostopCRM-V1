from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from .source_registry import PARTS_CATALOG_SOURCES, PARTS_PRICE_SOURCES, trusted_domains
from .web_tools import DuckDuckGoSearchClient, InternetToolError


_PRICE_PATTERN = re.compile(r"(\d[\d\s]{2,}(?:[.,]\d{1,2})?)\s*(₽|руб(?:\.|лей|ля)?|KZT|₸|\$|€)", re.I)
_DEFAULT_SERVICE_TYPE = "ТО"
_MAINTENANCE_HINTS = ("то", "техобслуж", "техничес", "service", "oil")
_BRAKE_HINTS = ("торм", "brake")
_SUSPENSION_HINTS = ("подвес", "ходов", "suspension")
_SPARK_HINTS = ("свеч", "spark")


class AutomotiveLookupService:
    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        self._timeout_seconds = timeout_seconds
        self._search = DuckDuckGoSearchClient(timeout_seconds=timeout_seconds)

    def decode_vin(self, vin: str) -> dict[str, Any]:
        normalized_vin = str(vin or "").strip().upper()
        if len(normalized_vin) < 11:
            raise InternetToolError("VIN is required and must be at least 11 characters.")
        url = (
            "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/"
            + quote(normalized_vin)
            + "?format=json"
        )
        try:
            with httpx.Client(timeout=self._timeout_seconds, headers={"User-Agent": "Mozilla/5.0 AutoStopCRM/1.0"}) as client:
                response = client.get(url, follow_redirects=True)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise InternetToolError(f"VIN decode failed: {exc}") from exc
        payload = response.json()
        results = payload.get("Results") or []
        row = results[0] if isinstance(results, list) and results else {}
        if not isinstance(row, dict):
            row = {}
        return {
            "vin": normalized_vin,
            "make": self._text(row.get("Make")),
            "model": self._text(row.get("Model")),
            "model_year": self._text(row.get("ModelYear")),
            "vehicle_type": self._text(row.get("VehicleType")),
            "body_class": self._text(row.get("BodyClass")),
            "engine_model": self._join_values(row.get("EngineModel"), row.get("DisplacementL"), row.get("FuelTypePrimary")),
            "transmission": self._join_values(row.get("TransmissionStyle"), row.get("TransmissionSpeeds")),
            "drive_type": self._text(row.get("DriveType")),
            "plant_country": self._text(row.get("PlantCountry")),
            "plant_company": self._text(row.get("Manufacturer")),
            "error_code": self._text(row.get("ErrorCode")),
            "source": "NHTSA vPIC",
            "source_url": url,
        }

    def search_part_numbers(self, *, vehicle_context: dict[str, Any] | None, part_query: str, limit: int = 8) -> dict[str, Any]:
        normalized_query = self._required_query(part_query)
        context = self._normalize_vehicle_context(vehicle_context)
        queries = [
            self._build_vehicle_query(context, normalized_query, suffix="OEM part number"),
            self._build_vehicle_query(context, normalized_query, suffix="catalog"),
        ]
        if context.get("vin"):
            queries.insert(0, f'{context["vin"]} {normalized_query} OEM part number')
        results = self._search_domains(
            queries,
            allowed_domains=trusted_domains(kind="catalog"),
            per_query_limit=max(2, limit),
            total_limit=limit,
        )
        return {
            "vehicle_context": context,
            "part_query": normalized_query,
            "results": results,
            "source_group": [item.label for item in PARTS_CATALOG_SOURCES],
        }

    def find_part_numbers(self, *, query: str, vehicle: dict[str, Any] | str | None = None, limit: int = 5) -> dict[str, Any]:
        vehicle_context = vehicle if isinstance(vehicle, dict) else {"vehicle": str(vehicle or "").strip()}
        return self.search_part_numbers(vehicle_context=vehicle_context, part_query=query, limit=limit)

    def lookup_part_prices(
        self,
        *,
        vehicle_context: dict[str, Any] | None,
        part_number_or_query: str,
        limit: int = 8,
    ) -> dict[str, Any]:
        normalized_query = self._required_query(part_number_or_query)
        context = self._normalize_vehicle_context(vehicle_context)
        quoted = f'"{normalized_query}"'
        queries = [
            f"{quoted} price",
            self._build_vehicle_query(context, normalized_query, suffix="price"),
        ]
        search_results = self._search_domains(
            queries,
            allowed_domains=trusted_domains(kind="price") + trusted_domains(kind="catalog"),
            per_query_limit=max(3, limit),
            total_limit=limit,
        )
        enriched: list[dict[str, Any]] = []
        for item in search_results:
            price_matches = self._extract_prices(" ".join([item.get("title", ""), item.get("snippet", "")]))
            page_excerpt = ""
            if not price_matches:
                try:
                    fetched = self._search.fetch_page_excerpt(item.get("url", ""), max_chars=1800)
                    page_excerpt = str(fetched.get("excerpt", "") or "")
                    price_matches = self._extract_prices(page_excerpt)
                except InternetToolError:
                    page_excerpt = ""
            enriched.append(
                {
                    **item,
                    "prices": price_matches,
                    "page_excerpt": page_excerpt[:500] if page_excerpt else "",
                }
            )
        return {
            "vehicle_context": context,
            "query": normalized_query,
            "results": enriched,
            "source_group": [item.label for item in PARTS_PRICE_SOURCES],
        }

    def estimate_price_ru(self, *, part_number: str, vehicle: dict[str, Any] | str | None = None, limit: int = 5) -> dict[str, Any]:
        vehicle_context = vehicle if isinstance(vehicle, dict) else {"vehicle": str(vehicle or "").strip()}
        payload = self.lookup_part_prices(
            vehicle_context=vehicle_context,
            part_number_or_query=part_number,
            limit=limit,
        )
        payload["part_number"] = str(part_number or "").strip()
        payload["market"] = "ru"
        return payload

    def estimate_maintenance(
        self,
        *,
        vehicle_context: dict[str, Any] | None,
        service_type: str = _DEFAULT_SERVICE_TYPE,
    ) -> dict[str, Any]:
        context = self._normalize_vehicle_context(vehicle_context)
        normalized_service = self._normalize_service_type(service_type)
        lower = normalized_service.casefold()
        works = [{"name": "Диагностика и осмотр автомобиля", "quantity": "1"}]
        materials = [
            {"name": "Моторное масло", "quantity": "1"},
            {"name": "Масляный фильтр", "quantity": "1"},
        ]
        notes = [
            "Список работ и материалов предварительный.",
            "Для точной цены по материалам используйте lookup_part_prices после уточнения каталожных номеров.",
        ]
        if lower == "то" or self._contains_any(lower, _MAINTENANCE_HINTS):
            self._append_unique_rows(
                works,
                [
                    {"name": "Замена моторного масла", "quantity": "1"},
                    {"name": "Замена масляного фильтра", "quantity": "1"},
                    {"name": "Контроль технических жидкостей", "quantity": "1"},
                ],
            )
            self._append_unique_rows(
                materials,
                [
                    {"name": "Прокладка сливной пробки", "quantity": "1"},
                    {"name": "Фильтр салона", "quantity": "1"},
                    {"name": "Воздушный фильтр двигателя", "quantity": "1"},
                ],
            )
        if self._contains_any(lower, _BRAKE_HINTS):
            self._append_unique_rows(works, [{"name": "Осмотр тормозной системы", "quantity": "1"}])
            self._append_unique_rows(materials, [{"name": "Тормозная жидкость", "quantity": "1"}])
        if self._contains_any(lower, _SUSPENSION_HINTS):
            self._append_unique_rows(works, [{"name": "Диагностика подвески", "quantity": "1"}])
        if self._contains_any(lower, _SPARK_HINTS):
            self._append_unique_rows(materials, [{"name": "Комплект свечей зажигания", "quantity": "1"}])
            self._append_unique_rows(works, [{"name": "Замена свечей зажигания", "quantity": "1"}])
        if context.get("vin"):
            notes.append("VIN доступен: можно выполнить точный подбор расходников и каталожных номеров.")
        return {
            "vehicle_context": context,
            "service_type": normalized_service,
            "works": works,
            "materials": materials,
            "notes": notes,
        }

    def search_web(self, *, query: str, limit: int = 5, allowed_domains: list[str] | None = None) -> dict[str, Any]:
        results = [item.to_dict() for item in self._search.search(query, limit=limit, allowed_domains=allowed_domains)]
        return {
            "query": str(query or "").strip(),
            "results": results,
        }

    def decode_dtc(self, *, code: str, limit: int = 5) -> dict[str, Any]:
        normalized_code = self._required_query(code).upper()
        query = f"{normalized_code} DTC code meaning"
        return {
            "code": normalized_code,
            "results": [item.to_dict() for item in self._search.search(query, limit=limit, allowed_domains=trusted_domains(kind="dtc"))],
            "source_group": ["DTC lookup"],
        }

    def search_fault_info(self, *, query: str, limit: int = 5) -> dict[str, Any]:
        normalized_query = self._required_query(query)
        return {
            "query": normalized_query,
            "results": [item.to_dict() for item in self._search.search(normalized_query, limit=limit, allowed_domains=trusted_domains(kind="fault"))],
            "source_group": ["Fault search"],
        }

    def fetch_page_excerpt(self, *, url: str, max_chars: int = 2500) -> dict[str, Any]:
        return self._search.fetch_page_excerpt(url, max_chars=max_chars)

    def _required_query(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise InternetToolError("part query is required")
        return text

    def _normalize_vehicle_context(self, vehicle_context: dict[str, Any] | None) -> dict[str, Any]:
        payload = vehicle_context if isinstance(vehicle_context, dict) else {}
        make = self._text(payload.get("make") or payload.get("make_display"))
        model = self._text(payload.get("model") or payload.get("model_display"))
        year = self._text(payload.get("year") or payload.get("production_year"))
        vehicle = self._text(payload.get("vehicle"))
        if not vehicle:
            vehicle = " ".join(part for part in (make, model, year) if part).strip()
        return {
            "make": make,
            "model": model,
            "year": year,
            "engine": self._text(payload.get("engine") or payload.get("engine_model")),
            "vin": self._text(payload.get("vin")),
            "vehicle": vehicle,
        }

    def _build_vehicle_query(self, context: dict[str, Any], item_query: str, *, suffix: str = "") -> str:
        parts = [
            context.get("make", ""),
            context.get("model", ""),
            context.get("year", ""),
            context.get("engine", ""),
            item_query,
            suffix,
        ]
        return " ".join(part for part in parts if part).strip()

    def _search_domains(
        self,
        queries: list[str],
        *,
        allowed_domains: list[str],
        per_query_limit: int,
        total_limit: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for query in queries:
            if not query:
                continue
            try:
                batch = self._search.search(query, limit=per_query_limit, allowed_domains=allowed_domains)
            except InternetToolError:
                continue
            for result in batch:
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                items.append(result.to_dict())
                if len(items) >= total_limit:
                    return items
        return items

    def _extract_prices(self, text: str) -> list[dict[str, str]]:
        prices: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for amount, currency in _PRICE_PATTERN.findall(str(text or "")):
            key = (amount.strip(), currency.strip())
            if key in seen:
                continue
            seen.add(key)
            prices.append({"amount": amount.strip(), "currency": currency.strip()})
        return prices[:5]

    def _normalize_service_type(self, value: str) -> str:
        text = str(value or "").strip()
        if text in {"РўРћ", "Ð¢Ðž"}:
            return "ТО"
        return text or _DEFAULT_SERVICE_TYPE

    def _text(self, value: Any) -> str:
        return str(value or "").strip()

    def _join_values(self, *values: Any) -> str:
        parts: list[str] = []
        for value in values:
            text = self._text(value)
            if text:
                parts.append(text)
        return " / ".join(parts)

    def _contains_any(self, haystack: str, needles: tuple[str, ...]) -> bool:
        return any(needle in haystack for needle in needles)

    def _append_unique_rows(self, rows: list[dict[str, str]], additions: list[dict[str, str]]) -> None:
        existing = {str(item.get("name") or "").strip().casefold() for item in rows}
        for item in additions:
            name = str(item.get("name") or "").strip()
            if not name or name.casefold() in existing:
                continue
            rows.append(item)
            existing.add(name.casefold())
