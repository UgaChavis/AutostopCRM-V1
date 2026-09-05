from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from ..models import CARD_DESCRIPTION_LIMIT, CARD_TITLE_LIMIT, normalize_text
from ..vehicle_profile import (
    VEHICLE_META_FIELDS,
    VEHICLE_PRIMARY_FIELDS,
    VIN_SOFT_PATTERN,
    VehicleProfile,
    build_vehicle_display,
    normalize_source_confidence,
    normalize_vehicle_field_names,
    normalize_vehicle_float,
    normalize_vehicle_int,
    normalize_vehicle_text,
    soft_normalize_vin,
    split_vehicle_display_alias,
)

_MAKE_ALIASES: dict[str, tuple[str, ...]] = {
    "TOYOTA": ("TOYOTA", "ТОЙОТА"),
    "KIA": ("KIA", "КИА"),
    "HYUNDAI": ("HYUNDAI", "ХЕНДАЙ", "ХЕНДЭ"),
    "NISSAN": ("NISSAN", "НИССАН"),
    "MITSUBISHI": ("MITSUBISHI", "МИТСУБИСИ"),
    "LADA": ("LADA", "ВАЗ", "ЛАДА"),
    "SUZUKI": ("SUZUKI", "СУЗУКИ"),
    "BMW": ("BMW", "БМВ"),
    "MERCEDES-BENZ": ("MERCEDES", "MERCEDES-BENZ", "МЕРСЕДЕС"),
    "VOLKSWAGEN": ("VOLKSWAGEN", "VW", "ФОЛЬКСВАГЕН"),
    "SKODA": ("SKODA", "ШКОДА"),
    "RENAULT": ("RENAULT", "РЕНО"),
    "FORD": ("FORD", "ФОРД"),
    "HONDA": ("HONDA", "ХОНДА"),
    "MAZDA": ("MAZDA", "МАЗДА"),
    "SUBARU": ("SUBARU", "СУБАРУ"),
    "AUDI": ("AUDI", "АУДИ"),
    "LEXUS": ("LEXUS", "ЛЕКСУС"),
}

_GEARBOX_PATTERNS: tuple[tuple[str, str], ...] = (
    ("CVT", r"\bCVT\b|ВАРИАТОР"),
    ("automatic", r"\bAT\b|АКПП|АВТОМАТ"),
    ("manual", r"\bMT\b|МКПП|МЕХАНИКА"),
    ("robot", r"РОБОТ|РОБОТИЗИРОВАН"),
    ("dsg", r"\bDSG\b"),
)

_DRIVETRAIN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("AWD", r"\bAWD\b|\b4WD\b|\b4X4\b|QUATTRO|ALL WHEEL DRIVE|ПОЛНЫЙ ПРИВОД"),
    ("FWD", r"\bFWD\b|FRONT WHEEL DRIVE|ПЕРЕДНИЙ ПРИВОД"),
    ("RWD", r"\bRWD\b|REAR WHEEL DRIVE|ЗАДНИЙ ПРИВОД"),
)

_FUEL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("diesel", r"ДИЗЕЛ|DIESEL"),
    ("hybrid", r"ГИБРИД|HYBRID"),
    ("gasoline", r"БЕНЗИН|GASOLINE|PETROL"),
    ("gas", r"\bLPG\b|ГАЗ"),
    ("electric", r"ЭЛЕКТРО|ELECTRIC|EV\b"),
)

_CAPACITY_UNIT_PATTERN = r"(?:ЛИТР(?:А|ОВ)?|Л|L|LITER(?:S)?)\b"
_OIL_ENGINE_PATTERN = re.compile(
    rf"(?:МОТОРНОЕ МАСЛО|МАСЛО ДВИГАТЕЛЯ|ENGINE OIL)[^0-9]{{0,20}}(\d+(?:[.,]\d+)?)\s*{_CAPACITY_UNIT_PATTERN}",
    re.IGNORECASE,
)
_OIL_GEARBOX_PATTERN = re.compile(
    rf"(?:МАСЛО КОРОБКИ|ATF|GEARBOX OIL|TRANSMISSION OIL)[^0-9]{{0,20}}(\d+(?:[.,]\d+)?)\s*{_CAPACITY_UNIT_PATTERN}",
    re.IGNORECASE,
)
_COOLANT_PATTERN = re.compile(
    rf"(?:ОХЛАЖДАЮЩАЯ ЖИДКОСТЬ|АНТИФРИЗ|COOLANT)[^0-9]{{0,20}}(\d+(?:[.,]\d+)?)\s*{_CAPACITY_UNIT_PATTERN}",
    re.IGNORECASE,
)
_POWER_PATTERN = re.compile(r"(\d{2,4})\s*(?:Л\.?\s*С\.?|HP|ЛС)\b", re.IGNORECASE)
_DISPLACEMENT_PATTERN = re.compile(r"(\d(?:[.,]\d{1,2})?)\s*(?:Л|L)\b", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(19\d{2}|20\d{2}|21\d{2})\b")
_MILEAGE_PATTERN = re.compile(
    r"(?:ПРОБЕГ|MILEAGE|ОДОМЕТР)\s*[:\-]?\s*([\d\s]{2,12})", re.IGNORECASE
)
_ENGINE_LABEL_PATTERN = re.compile(
    r"(?:ENGINE(?:\s+MODEL)?|ДВИГАТЕЛЬ|МОТОР)\s*[:\-]\s*([A-Z0-9\-/. ]{3,32})", re.IGNORECASE
)
_ENGINE_CODE_PATTERN = re.compile(
    r"(?:ENGINE\s+CODE|КОД\s+ДВИГАТЕЛЯ|ENGINE NO|ДВИГАТЕЛЬ №)\s*[:\-]?\s*([A-Z0-9\-]{3,24})",
    re.IGNORECASE,
)
_GEARBOX_LABEL_PATTERN = re.compile(
    r"(?:GEARBOX|TRANSMISSION|КОРОБКА|ТРАНСМИССИЯ)\s*[:\-]?\s*([A-Z0-9\-/. ]{2,32})", re.IGNORECASE
)
_PHONE_PATTERN = re.compile(
    r"(?:\+7|8)\s*(?:\(\s*\d{3}\s*\)|\d{3})\s*[\- ]?\s*\d{3}\s*[\- ]?\s*\d{2}\s*[\- ]?\s*\d{2}"
)
_CUSTOMER_NAME_PATTERN = re.compile(
    r"(?:КЛИЕНТ|ВЛАДЕЛЕЦ|КОНТАКТ(?:НОЕ ЛИЦО)?|CUSTOMER)\s*[:\-]?\s*([A-ZА-ЯЁ][A-ZА-ЯЁA-Zа-яё.\-]+(?:\s+[A-ZА-ЯЁ][A-ZА-ЯЁA-Zа-яё.\-]+){0,2})",
    re.IGNORECASE,
)
_GENERATION_LABEL_PATTERN = re.compile(
    r"(?:ПОКОЛЕНИЕ|КУЗОВ|ПЛАТФОРМА|PLATFORM|GENERATION|BODY)\s*[:\-]?\s*([A-ZА-Я0-9\-/. ]{1,32})",
    re.IGNORECASE,
)
_PLATFORM_TOKEN_PATTERN = re.compile(r"\b([A-Z]{1,4}\d{1,4}[A-Z]?|[IVX]{1,5})\b", re.IGNORECASE)
_GEARBOX_MODEL_FALLBACK_PATTERN = re.compile(
    r"\b(?:DQ\d{3,4}|DL\d{3,4}|JF\d{3,4}[A-Z]?|RE\d{2}[A-Z]\d{2}[A-Z]?|TF-\d{2,3}[A-Z]*|A6GF1|UA80E|8HP\d{2}|6T\d{2}|09G|01M|AISIN)\b",
    re.IGNORECASE,
)
_GENERIC_ENGINE_MODEL_VALUES = {
    "SIZE",
    "POWER",
    "SPECS",
    "SPECIFICATIONS",
    "REVIEW",
    "DATA",
    "TECHNICAL",
    "DIMENSIONS",
    "FUEL",
    "CONSUMPTION",
}
_BOLT_PATTERN = re.compile(r"\b([45]x1\d{2}(?:[.,]\d)?)\b", re.IGNORECASE)
_MAKE_MODEL_SPLIT_PATTERN = re.compile(r"[^A-ZА-Я0-9]+", re.IGNORECASE)
_PROBLEM_MARKER_PATTERN = re.compile(
    r"(?:ПРОБЛЕМА|ЖАЛОБА|СИМПТОМ|НЕИСПРАВНОСТЬ|НУЖНО|ЗАДАЧА|РЕМОНТ|ПРОВЕРИТЬ)\s*[:\-]?\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)


_MODEL_ALIASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Suzuki", "Swift", ("SWIFT",)),
    ("Kia", "Rio", ("RIO",)),
    ("Toyota", "Camry", ("CAMRY", "CAMRY 70", "XV70")),
    ("Nissan", "X-Trail", ("X-TRAIL", "XTRAIL", "T32")),
    ("Lada", "Vesta", ("VESTA",)),
)


class VehicleProfileService:
    def normalize_profile_payload(
        self,
        raw_profile: Any,
        *,
        assume_manual_for_explicit_fields: bool = False,
    ) -> tuple[VehicleProfile, set[str], set[str]]:
        raw = self._normalize_profile_alias_payload(raw_profile)
        profile = VehicleProfile.from_dict(raw)
        present_primary = {field for field in VEHICLE_PRIMARY_FIELDS if field in raw}
        present_meta = {field for field in VEHICLE_META_FIELDS if field in raw}

        manual_fields = set(profile.manual_fields)
        autofilled_fields = set(profile.autofilled_fields)
        tentative_fields = set(profile.tentative_fields)
        has_meta_hints = any(
            key in raw for key in ("manual_fields", "autofilled_fields", "tentative_fields")
        )

        if assume_manual_for_explicit_fields and present_primary and not has_meta_hints:
            manual_fields.update(present_primary)
        elif not manual_fields and not autofilled_fields and present_primary:
            manual_fields.update(
                {field for field in present_primary if raw.get(field) not in (None, "", [])}
            )

        autofilled_fields -= manual_fields
        tentative_fields &= autofilled_fields

        profile.manual_fields = sorted(normalize_vehicle_field_names(list(manual_fields)))
        profile.autofilled_fields = sorted(normalize_vehicle_field_names(list(autofilled_fields)))
        profile.tentative_fields = sorted(normalize_vehicle_field_names(list(tentative_fields)))
        if not profile.manual_fields and not profile.autofilled_fields:
            profile.data_completion_state = "manually_entered"
        return profile, present_primary, present_meta

    def _normalize_profile_alias_payload(self, raw_profile: Any) -> dict[str, Any]:
        raw = dict(raw_profile) if isinstance(raw_profile, dict) else {}
        display_name_present = "display_name" in raw
        display_name = normalize_vehicle_text(raw.get("display_name"))
        display_name_manual = self._profile_field_name_present(
            raw.get("manual_fields"), "display_name"
        )
        current_display = build_vehicle_display(
            normalize_vehicle_text(raw.get("make_display")),
            normalize_vehicle_text(raw.get("model_display")),
            normalize_vehicle_int(raw.get("production_year")),
        )
        if display_name_present and (display_name_manual or display_name != current_display):
            make_display, model_display = split_vehicle_display_alias(
                display_name, normalize_vehicle_int(raw.get("production_year"))
            )
            raw["make_display"] = make_display
            raw["model_display"] = model_display

        if "registration_plate" not in raw and "license_plate" in raw:
            raw["registration_plate"] = raw.get("license_plate")

        for meta_field in ("manual_fields", "autofilled_fields", "tentative_fields"):
            if meta_field in raw:
                raw[meta_field] = self._normalize_profile_alias_field_names(raw.get(meta_field))
        if isinstance(raw.get("field_sources"), dict):
            raw["field_sources"] = self._normalize_profile_alias_field_sources(raw["field_sources"])
        return raw

    @staticmethod
    def _profile_field_names(value: Any) -> list[str]:
        if isinstance(value, str):
            raw_names = re.split(r"[\s,;]+", value)
        elif isinstance(value, list):
            raw_names = [str(item) for item in value]
        else:
            return []
        names: list[str] = []
        for raw_name in raw_names:
            name = str(raw_name or "").strip()
            if name:
                names.append(name)
        return names

    def _profile_field_name_present(self, value: Any, field_name: str) -> bool:
        return field_name in self._profile_field_names(value)

    def _normalize_profile_alias_field_names(self, value: Any) -> list[str]:
        names: list[str] = []
        for field_name in self._profile_field_names(value):
            if field_name == "display_name":
                names.extend(["make_display", "model_display"])
            elif field_name == "license_plate":
                names.append("registration_plate")
            else:
                names.append(field_name)
        return names

    @staticmethod
    def _normalize_profile_alias_field_sources(value: dict[str, Any]) -> dict[str, Any]:
        sources: dict[str, Any] = {}
        for field_name, source in value.items():
            if field_name == "display_name":
                sources["make_display"] = source
                sources["model_display"] = source
            elif field_name == "license_plate":
                sources["registration_plate"] = source
            else:
                sources[field_name] = source
        return sources

    def merge_profile_patch(
        self,
        existing: VehicleProfile | None,
        incoming: VehicleProfile,
        *,
        present_primary: set[str],
        present_meta: set[str],
    ) -> tuple[VehicleProfile, list[str]]:
        result = deepcopy(existing) if existing is not None else VehicleProfile()
        changed_fields: list[str] = []

        for field_name in VEHICLE_PRIMARY_FIELDS:
            if field_name not in present_primary:
                continue
            previous = getattr(result, field_name)
            next_value = getattr(incoming, field_name)
            if previous != next_value:
                setattr(result, field_name, deepcopy(next_value))
                changed_fields.append(field_name)

        for field_name in VEHICLE_META_FIELDS:
            if field_name not in present_meta:
                continue
            setattr(result, field_name, deepcopy(getattr(incoming, field_name)))

        manual_fields = set(result.manual_fields)
        autofilled_fields = set(result.autofilled_fields)
        tentative_fields = set(result.tentative_fields)

        if "manual_fields" not in present_meta and present_primary:
            manual_fields.update(present_primary)
        if "autofilled_fields" in present_meta:
            autofilled_fields = set(result.autofilled_fields)
        else:
            autofilled_fields -= set(changed_fields)
        if "tentative_fields" in present_meta:
            tentative_fields = set(result.tentative_fields)
        else:
            tentative_fields -= set(changed_fields)

        autofilled_fields -= manual_fields
        tentative_fields &= autofilled_fields

        result.manual_fields = sorted(normalize_vehicle_field_names(list(manual_fields)))
        result.autofilled_fields = sorted(normalize_vehicle_field_names(list(autofilled_fields)))
        result.tentative_fields = sorted(normalize_vehicle_field_names(list(tentative_fields)))
        result.data_completion_state = self._derive_completion_state(result)
        result.source_confidence = normalize_source_confidence(result.source_confidence)
        return result, changed_fields

    def finalize_profile_metadata(self, profile: VehicleProfile) -> VehicleProfile:
        result = deepcopy(profile)
        manual_fields = set(normalize_vehicle_field_names(result.manual_fields))
        autofilled_fields = set(normalize_vehicle_field_names(result.autofilled_fields))
        tentative_fields = set(normalize_vehicle_field_names(result.tentative_fields))
        non_empty_primary = {
            field_name
            for field_name in VEHICLE_PRIMARY_FIELDS
            if not self._is_empty_vehicle_value(getattr(result, field_name))
        }

        if not manual_fields and not autofilled_fields:
            manual_fields.update(non_empty_primary)

        manual_fields &= non_empty_primary
        autofilled_fields &= non_empty_primary
        tentative_fields &= autofilled_fields

        result.manual_fields = sorted(normalize_vehicle_field_names(list(manual_fields)))
        result.autofilled_fields = sorted(
            normalize_vehicle_field_names(list(autofilled_fields - manual_fields))
        )
        result.tentative_fields = sorted(normalize_vehicle_field_names(list(tentative_fields)))
        result.data_completion_state = self._derive_completion_state(result)

        if not result.source_summary.strip():
            inferred_summary = self._infer_source_summary(result)
            if inferred_summary:
                result.source_summary = inferred_summary
        result.source_confidence = normalize_source_confidence(result.source_confidence)
        if result.source_confidence <= 0:
            result.source_confidence = self._derive_confidence(result)
        else:
            result.source_confidence = normalize_source_confidence(result.source_confidence)
        result.warnings = self._normalize_warnings(result.warnings)
        return result

    def _populate_text_payload_identifiers(
        self, profile: VehicleProfile, combined_text: str, upper_text: str
    ) -> None:
        vin_match = VIN_SOFT_PATTERN.search(upper_text)
        if vin_match:
            profile.vin = soft_normalize_vin(vin_match.group(1))

        year_match = _YEAR_PATTERN.search(combined_text)
        if year_match:
            profile.production_year = normalize_vehicle_int(year_match.group(1))

        mileage_match = _MILEAGE_PATTERN.search(combined_text)
        if mileage_match:
            profile.mileage = normalize_vehicle_int(re.sub(r"\s+", "", mileage_match.group(1)))

        phone_match = _PHONE_PATTERN.search(combined_text)
        if phone_match:
            profile.customer_phone = self._format_phone(phone_match.group(0))

        customer_name = self._extract_customer_name(combined_text)
        if customer_name:
            profile.customer_name = customer_name

    def _populate_text_payload_make(self, profile: VehicleProfile, upper_text: str) -> str:
        for canonical_make, aliases in _MAKE_ALIASES.items():
            if any(
                re.search(rf"\b{re.escape(alias)}\b", upper_text, re.IGNORECASE)
                for alias in aliases
            ):
                profile.make_display = self._display_make(canonical_make)
                return canonical_make
        return ""

    def _populate_text_payload_model_and_engine(
        self,
        profile: VehicleProfile,
        *,
        text: str,
        explicit_vehicle: str,
        combined_text: str,
        upper_text: str,
        detected_make: str,
    ) -> None:
        model_source_text = text or explicit_vehicle or combined_text
        model_match = self._detect_model(model_source_text, detected_make)
        if model_match:
            profile.model_display = model_match
            generation_or_platform = self._extract_generation_or_platform(
                combined_text, model_match
            )
            if generation_or_platform:
                profile.generation_or_platform = generation_or_platform

        engine_code_match = _ENGINE_CODE_PATTERN.search(upper_text)
        if engine_code_match:
            profile.engine_code = normalize_vehicle_text(engine_code_match.group(1), limit=24)

        engine_model_match = _ENGINE_LABEL_PATTERN.search(combined_text)
        if engine_model_match:
            candidate = normalize_vehicle_text(engine_model_match.group(1), limit=40)
            candidate = re.split(
                r"\b(?:TRANSMISSION|GEARBOX|DRIVETRAIN|КПП|КОРОБКА)\b",
                candidate,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].rstrip(" .,:;-")
            candidate_upper = candidate.upper()
            if (
                candidate
                and candidate_upper
                not in {profile.make_display.upper(), profile.model_display.upper()}
                and candidate_upper not in _GENERIC_ENGINE_MODEL_VALUES
            ):
                profile.engine_model = candidate

        displacement_match = _DISPLACEMENT_PATTERN.search(combined_text)
        if displacement_match:
            profile.engine_displacement_l = normalize_vehicle_float(displacement_match.group(1))

        power_match = _POWER_PATTERN.search(upper_text)
        if power_match:
            profile.engine_power_hp = normalize_vehicle_int(power_match.group(1))

    def _populate_text_payload_transmission(
        self, profile: VehicleProfile, combined_text: str, upper_text: str
    ) -> None:
        for gearbox_type, pattern in _GEARBOX_PATTERNS:
            if re.search(pattern, upper_text, re.IGNORECASE):
                profile.gearbox_type = gearbox_type
                break
        gearbox_label_match = _GEARBOX_LABEL_PATTERN.search(combined_text)
        if gearbox_label_match:
            candidate = normalize_vehicle_text(gearbox_label_match.group(1), limit=40)
            if candidate and self._looks_like_gearbox_model(candidate):
                profile.gearbox_model = candidate
        elif not profile.gearbox_model:
            gearbox_model_hint = self._extract_gearbox_model_hint(combined_text)
            if gearbox_model_hint and self._looks_like_gearbox_model(gearbox_model_hint):
                profile.gearbox_model = gearbox_model_hint

    def _populate_text_payload_powertrain_and_misc(
        self, profile: VehicleProfile, combined_text: str, upper_text: str
    ) -> None:
        for drivetrain, pattern in _DRIVETRAIN_PATTERNS:
            if re.search(pattern, upper_text, re.IGNORECASE):
                profile.drivetrain = drivetrain
                break

        for fuel_type, pattern in _FUEL_PATTERNS:
            if re.search(pattern, upper_text, re.IGNORECASE):
                profile.fuel_type = fuel_type
                break

        profile.oil_engine_capacity_l = self._extract_capacity(_OIL_ENGINE_PATTERN, combined_text)
        profile.oil_gearbox_capacity_l = self._extract_capacity(_OIL_GEARBOX_PATTERN, combined_text)
        profile.coolant_capacity_l = self._extract_capacity(_COOLANT_PATTERN, combined_text)

        bolt_match = _BOLT_PATTERN.search(upper_text)
        if bolt_match:
            profile.wheel_bolt_pattern = bolt_match.group(1).replace(",", ".").upper()

    def _build_text_payload_metadata(
        self,
        profile: VehicleProfile,
        *,
        text: str,
        combined_text: str,
        explicit_title: str,
        explicit_description: str,
        warnings: list[str],
    ) -> tuple[str, str]:
        title_candidate = explicit_title.strip()
        description_candidate = explicit_description.strip()
        if not title_candidate:
            title_candidate = self._build_issue_title(combined_text)
        if not description_candidate:
            description_candidate = normalize_text(text, default="", limit=CARD_DESCRIPTION_LIMIT)

        if profile.vin and len(profile.vin) != 17:
            warnings.append(
                "VIN выглядит неполным: автодополнение из интернета может быть ограничено."
            )

        return title_candidate, description_candidate

    def _parse_text_payload(
        self,
        raw_text: str,
        *,
        explicit_vehicle: str = "",
        explicit_title: str = "",
        explicit_description: str = "",
    ) -> tuple[VehicleProfile, dict[str, str], list[str]]:
        text = str(raw_text or "").strip()
        profile = VehicleProfile(raw_input_text=text)
        warnings: list[str] = []
        if not text and not explicit_vehicle:
            return profile, {"title": explicit_title, "description": explicit_description}, warnings

        combined_text = " ".join(part for part in (explicit_vehicle, text) if part).strip()
        upper_text = combined_text.upper()
        self._populate_text_payload_identifiers(profile, combined_text, upper_text)
        detected_make = self._populate_text_payload_make(profile, upper_text)
        self._populate_text_payload_model_and_engine(
            profile,
            text=text,
            explicit_vehicle=explicit_vehicle,
            combined_text=combined_text,
            upper_text=upper_text,
            detected_make=detected_make,
        )
        self._populate_text_payload_transmission(profile, combined_text, upper_text)
        self._populate_text_payload_powertrain_and_misc(profile, combined_text, upper_text)

        non_empty_fields = {
            field_name
            for field_name in VEHICLE_PRIMARY_FIELDS
            if not self._is_empty_vehicle_value(getattr(profile, field_name))
        }
        profile.manual_fields = sorted(non_empty_fields)
        profile.data_completion_state = "manually_entered"
        title_candidate, description_candidate = self._build_text_payload_metadata(
            profile,
            text=text,
            combined_text=combined_text,
            explicit_title=explicit_title,
            explicit_description=explicit_description,
            warnings=warnings,
        )

        return profile, {"title": title_candidate, "description": description_candidate}, warnings

    def _extract_capacity(self, pattern: re.Pattern[str], text: str) -> float | None:
        match = pattern.search(text)
        if not match:
            return None
        return normalize_vehicle_float(match.group(1))

    def _display_make(self, make: str) -> str:
        normalized = normalize_vehicle_text(make)
        if normalized.upper() == "MERCEDES-BENZ":
            return "Mercedes-Benz"
        return normalized.title()

    def _detect_model_from_catalog(self, text: str, detected_make: str) -> str:
        upper_text = text.upper()
        for make, model, aliases in _MODEL_ALIASES:
            if detected_make and self._slug(make) != self._slug(detected_make):
                continue
            for alias in aliases:
                if re.search(rf"\b{re.escape(alias)}\b", upper_text, re.IGNORECASE):
                    return normalize_vehicle_text(model)
        return ""

    def _detect_model_from_make_alias(self, text: str, detected_make: str) -> str:
        upper_text = text.upper()
        for alias in _MAKE_ALIASES.get(detected_make, ()):
            if not alias or alias not in upper_text:
                continue
            tail = upper_text.split(alias, 1)[1]
            tokens = [token for token in _MAKE_MODEL_SPLIT_PATTERN.split(tail) if token]
            model_tokens: list[str] = []
            for token in tokens:
                if _YEAR_PATTERN.fullmatch(token):
                    break
                if token in {
                    "VIN",
                    "ПРОБЛЕМА",
                    "ЖАЛОБА",
                    "НЕИСПРАВНОСТЬ",
                    "SPECS",
                    "SPECIFICATIONS",
                    "SPEC",
                    "REVIEW",
                    "DATA",
                    "PERFORMANCE",
                    "PHOTOS",
                    "WARRANTY",
                    "TECHNICAL",
                    "DIMENSIONS",
                    "FUEL",
                    "CONSUMPTION",
                    "CATALOG",
                    "QUATTRO",
                    "SEDAN",
                    "COUPE",
                    "CABRIOLET",
                    "HATCHBACK",
                    "SPORTBACK",
                    "TOURING",
                }:
                    break
                if token.isdigit() and model_tokens:
                    break
                model_tokens.append(token)
                if len(model_tokens) >= 3:
                    break
            if model_tokens:
                return self._compose_model_tokens(model_tokens)
        return ""

    def _detect_model(self, text: str, detected_make: str) -> str:
        upper_text = text.upper()
        if detected_make == "MAZDA" and re.search(r"\bCX[\s-]?5\b", upper_text, re.IGNORECASE):
            return "CX-5"
        catalog_match = self._detect_model_from_catalog(text, detected_make)
        if catalog_match:
            return catalog_match
        if detected_make:
            make_alias_match = self._detect_model_from_make_alias(text, detected_make)
            if make_alias_match:
                return make_alias_match
        return ""

    def _compose_model_tokens(self, tokens: list[str]) -> str:
        normalized_tokens: list[str] = []
        for raw_token in tokens:
            token = normalize_vehicle_text(raw_token, limit=24)
            if not token:
                continue
            if re.fullmatch(r"[IVX]{1,5}", token, re.IGNORECASE):
                normalized_tokens.append(token.upper())
                continue
            if re.fullmatch(r"[A-Z]{1,4}\d{1,4}[A-Z]?", token, re.IGNORECASE):
                normalized_tokens.append(token.upper())
                continue
            normalized_tokens.append(token.title())
        return " ".join(normalized_tokens)

    def _extract_customer_name(self, text: str) -> str:
        match = _CUSTOMER_NAME_PATTERN.search(text)
        if not match:
            return ""
        blocked_tokens = {"ТЕЛЕФОН", "PHONE", "VIN", "ГОСНОМЕР", "ПРОБЕГ", "MILEAGE"}
        parts: list[str] = []
        for part in str(match.group(1) or "").strip().split():
            normalized = str(part or "").strip()
            if not normalized:
                continue
            if normalized.upper().strip(":.,-") in blocked_tokens:
                break
            parts.append(normalized)
        if not parts:
            return ""
        return " ".join(part[:1].upper() + part[1:].lower() for part in parts)[:80]

    def _format_phone(self, value: str) -> str:
        digits = re.sub(r"\D+", "", str(value or ""))
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        if len(digits) == 11 and digits.startswith("7"):
            return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
        return normalize_vehicle_text(value, limit=32)

    def _extract_generation_or_platform(self, text: str, model_display: str) -> str:
        labelled = _GENERATION_LABEL_PATTERN.search(text)
        if labelled:
            candidate = normalize_vehicle_text(labelled.group(1), limit=40)
            if candidate:
                return (
                    candidate.upper()
                    if re.fullmatch(r"[A-Z0-9\-/. ]+", candidate, re.IGNORECASE)
                    else candidate
                )
        model_tokens = [token for token in re.split(r"[\s\-]+", model_display.upper()) if token]
        if model_tokens:
            joined = r"[\s\-]+".join(re.escape(token) for token in model_tokens)
            near_model = re.search(
                rf"{joined}\s+([A-Z]{{1,4}}\d{{1,4}}[A-Z]?|[IVX]{{1,5}})\b",
                text.upper(),
                re.IGNORECASE,
            )
            if near_model:
                return near_model.group(1).upper()
        platform_tokens = [
            match.group(1).upper() for match in _PLATFORM_TOKEN_PATTERN.finditer(text.upper())
        ]
        filtered = [
            token
            for token in platform_tokens
            if not _YEAR_PATTERN.fullmatch(token)
            and token not in {"VIN", "AT", "MT", "CVT", "DSG", "FWD", "AWD", "RWD"}
        ]
        return filtered[0] if filtered else ""

    def _extract_gearbox_model_hint(self, text: str) -> str:
        match = _GEARBOX_MODEL_FALLBACK_PATTERN.search(text.upper())
        if not match:
            return ""
        return normalize_vehicle_text(match.group(0), limit=40).upper()

    def _looks_like_gearbox_model(self, candidate: str) -> bool:
        value = normalize_vehicle_text(candidate, limit=40).upper()
        if not value:
            return False
        if value in {"AT", "MT", "CVT", "DSG"}:
            return True
        if re.fullmatch(
            r"(?:DQ\d{3,4}|DL\d{3,4}|JF\d{3,4}[A-Z]?|RE\d{2}[A-Z]\d{2}[A-Z]?|TF-\d{2,3}[A-Z]*|A6GF1|UA80E|8HP\d{2}|6T\d{2}|09G|01M)",
            value,
        ):
            return True
        if value == "AISIN":
            return True
        return False

    def _build_issue_title(self, text: str) -> str:
        candidate = ""
        marker_match = _PROBLEM_MARKER_PATTERN.search(text.upper())
        if marker_match:
            candidate = marker_match.group(1)
        else:
            parts = re.split(r"[,.;\n]+", text, maxsplit=2)
            if parts:
                candidate = parts[-1]
        candidate = normalize_text(candidate, default="", limit=CARD_TITLE_LIMIT)
        candidate = re.sub(r"\b(VIN|ГОД|MAKE|MODEL)\b", "", candidate, flags=re.IGNORECASE)
        candidate = " ".join(candidate.split())
        if not candidate:
            return "НОВАЯ КАРТОЧКА ПО АВТО"
        return candidate[:CARD_TITLE_LIMIT].upper()

    def _infer_source_summary(self, profile: VehicleProfile) -> str:
        source_values = {
            str(value or "").strip().lower() for value in profile.field_sources.values()
        }
        links = [str(value or "").strip().lower() for value in profile.source_links_or_refs]
        if any("official_vin_decode" in value for value in source_values) or any(
            "vpic.nhtsa.dot.gov" in value for value in links
        ):
            return "official VIN decode"
        if any("structured_reference_catalog" in value for value in source_values) or any(
            "catalog:" in value for value in links
        ):
            return "reference catalog"
        if profile.autofilled_fields:
            return "autofilled from card content"
        if profile.manual_fields:
            return "manual entry"
        return ""

    def _derive_completion_state(self, profile: VehicleProfile) -> str:
        if profile.manual_fields and not profile.autofilled_fields:
            return "manually_entered"
        if profile.autofilled_fields and len(profile.autofilled_fields) >= 6:
            return "mostly_autofilled"
        if profile.autofilled_fields:
            return "partially_autofilled"
        return profile.data_completion_state or "manually_entered"

    def _derive_confidence(self, profile: VehicleProfile) -> float:
        base = normalize_source_confidence(profile.source_confidence)
        if profile.autofilled_fields:
            base = max(base, 0.48)
            if len(profile.autofilled_fields) >= 6:
                base = max(base, 0.68)
        if profile.manual_fields and not profile.autofilled_fields:
            base = max(base, 0.95)
        return round(max(0.0, min(1.0, base)), 2)

    def _normalize_warnings(self, values: list[str]) -> list[str]:
        warnings: list[str] = []
        for raw in values:
            warning = normalize_vehicle_text(raw, limit=200)
            if warning and warning not in warnings:
                warnings.append(warning)
        return warnings

    def _slug(self, value: str) -> str:
        return re.sub(r"[^a-z0-9а-яё]+", " ", str(value or "").strip().casefold()).strip()

    def _is_empty_vehicle_value(self, value: Any) -> bool:
        return value in (None, "", [], {}, ())
