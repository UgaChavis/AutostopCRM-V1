from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from ..models import CARD_DESCRIPTION_LIMIT, CARD_TITLE_LIMIT, CARD_VEHICLE_LIMIT, normalize_text
from ..vehicle_profile import (
    VEHICLE_META_FIELDS,
    VEHICLE_PRIMARY_FIELDS,
    VIN_SOFT_PATTERN,
    VehicleProfile,
    normalize_vehicle_field_names,
    normalize_vehicle_float,
    normalize_vehicle_int,
    normalize_vehicle_text,
    soft_normalize_vin,
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
    ("AWD", r"\bAWD\b|\b4WD\b|ПОЛНЫЙ ПРИВОД"),
    ("FWD", r"\bFWD\b|ПЕРЕДНИЙ ПРИВОД"),
    ("RWD", r"\bRWD\b|ЗАДНИЙ ПРИВОД"),
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
_MILEAGE_PATTERN = re.compile(r"(?:ПРОБЕГ|MILEAGE|ОДОМЕТР)\s*[:\-]?\s*([\d\s]{2,12})", re.IGNORECASE)
_ENGINE_LABEL_PATTERN = re.compile(r"(?:ENGINE(?:\s+MODEL)?|ДВИГАТЕЛЬ|МОТОР)\s*[:\-]?\s*([A-Z0-9\-/. ]{3,32})", re.IGNORECASE)
_ENGINE_CODE_PATTERN = re.compile(r"(?:ENGINE\s+CODE|КОД\s+ДВИГАТЕЛЯ|ENGINE NO|ДВИГАТЕЛЬ №)\s*[:\-]?\s*([A-Z0-9\-]{3,24})", re.IGNORECASE)
_GEARBOX_LABEL_PATTERN = re.compile(r"(?:GEARBOX|TRANSMISSION|КОРОБКА|ТРАНСМИССИЯ)\s*[:\-]?\s*([A-Z0-9\-/. ]{2,32})", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?:\+7|8)\s*(?:\(\s*\d{3}\s*\)|\d{3})\s*[\- ]?\s*\d{3}\s*[\- ]?\s*\d{2}\s*[\- ]?\s*\d{2}")
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
_BOLT_PATTERN = re.compile(r"\b([45]x1\d{2}(?:[.,]\d)?)\b", re.IGNORECASE)
_MAKE_MODEL_SPLIT_PATTERN = re.compile(r"[^A-ZА-Я0-9]+", re.IGNORECASE)
_PROBLEM_MARKER_PATTERN = re.compile(
    r"(?:ПРОБЛЕМА|ЖАЛОБА|СИМПТОМ|НЕИСПРАВНОСТЬ|НУЖНО|ЗАДАЧА|РЕМОНТ|ПРОВЕРИТЬ)\s*[:\-]?\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)
_VEHICLE_IMAGE_LIMIT_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class VehicleCatalogEntry:
    key: str
    make_display: str
    model_display: str
    year_from: int
    year_to: int
    model_aliases: tuple[str, ...]
    base_fields: dict[str, Any] = field(default_factory=dict)
    detailed_fields: dict[str, Any] = field(default_factory=dict)
    engine_codes: tuple[str, ...] = field(default_factory=tuple)
    engine_models: tuple[str, ...] = field(default_factory=tuple)
    displacements_l: tuple[float, ...] = field(default_factory=tuple)
    source_ref: str = ""


@dataclass(slots=True)
class ImageParseResult:
    raw_text: str = ""
    status: str = "not_attempted"
    warnings: list[str] = field(default_factory=list)
    debug_chunks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VehicleAutofillResult:
    vehicle_profile: VehicleProfile
    card_draft: dict[str, str]
    warnings: list[str]
    used_sources: list[str]
    image_parse_status: str
    image_debug_chunks: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "vehicle_profile": self.vehicle_profile.to_dict(),
            "card_draft": dict(self.card_draft),
            "warnings": list(self.warnings),
            "used_sources": list(self.used_sources),
            "image_parse_status": self.image_parse_status,
            "image_debug_chunks": list(self.image_debug_chunks),
        }


_CATALOG_ENTRIES: tuple[VehicleCatalogEntry, ...] = (
    VehicleCatalogEntry(
        key="suzuki_swift_azg_k12b",
        make_display="Suzuki",
        model_display="Swift",
        year_from=2011,
        year_to=2016,
        model_aliases=("SWIFT",),
        base_fields={
            "generation_or_platform": "AZG / ZC72S",
            "fuel_type": "gasoline",
            "wheel_bolt_pattern": "4x100",
            "brake_front_type": "disc",
            "brake_rear_type": "drum",
            "steering_system_type": "electric power steering",
            "oem_notes": "Компактный городской хэтчбек. Точные жидкости зависят от двигателя и коробки.",
        },
        detailed_fields={
            "engine_code": "K12B",
            "engine_model": "K12B",
            "engine_displacement_l": 1.2,
            "engine_power_hp": 94,
            "gearbox_type": "automatic",
            "drivetrain": "FWD",
            "oil_engine_capacity_l": 3.7,
            "coolant_capacity_l": 5.2,
        },
        engine_codes=("K12B",),
        engine_models=("K12B",),
        displacements_l=(1.2,),
        source_ref="catalog:minimal_kanban/suzuki_swift_azg_k12b",
    ),
    VehicleCatalogEntry(
        key="kia_rio_qb_g4fc",
        make_display="Kia",
        model_display="Rio",
        year_from=2015,
        year_to=2020,
        model_aliases=("RIO",),
        base_fields={
            "generation_or_platform": "QB / FB",
            "fuel_type": "gasoline",
            "wheel_bolt_pattern": "4x100",
            "brake_front_type": "ventilated disc",
            "brake_rear_type": "disc/drum by trim",
            "steering_system_type": "electric power steering",
        },
        detailed_fields={
            "engine_code": "G4FC",
            "engine_model": "Gamma 1.6 MPI",
            "engine_displacement_l": 1.6,
            "engine_power_hp": 123,
            "gearbox_type": "automatic",
            "drivetrain": "FWD",
            "oil_engine_capacity_l": 3.6,
            "oil_gearbox_capacity_l": 6.7,
            "coolant_capacity_l": 5.7,
        },
        engine_codes=("G4FC",),
        engine_models=("GAMMA 1.6 MPI", "G4FC"),
        displacements_l=(1.6,),
        source_ref="catalog:minimal_kanban/kia_rio_qb_g4fc",
    ),
    VehicleCatalogEntry(
        key="toyota_camry_xv70_a25a",
        make_display="Toyota",
        model_display="Camry",
        year_from=2018,
        year_to=2024,
        model_aliases=("CAMRY", "CAMRY 70", "XV70"),
        base_fields={
            "generation_or_platform": "XV70",
            "fuel_type": "gasoline",
            "wheel_bolt_pattern": "5x114.3",
            "brake_front_type": "ventilated disc",
            "brake_rear_type": "disc",
            "steering_system_type": "electric power steering",
        },
        detailed_fields={
            "engine_code": "A25A-FKS",
            "engine_model": "Dynamic Force 2.5",
            "engine_displacement_l": 2.5,
            "engine_power_hp": 200,
            "gearbox_type": "automatic",
            "gearbox_model": "UA80E",
            "drivetrain": "FWD",
            "oil_engine_capacity_l": 4.5,
            "oil_gearbox_capacity_l": 8.2,
            "coolant_capacity_l": 6.2,
        },
        engine_codes=("A25A-FKS",),
        engine_models=("A25A-FKS", "DYNAMIC FORCE 2.5"),
        displacements_l=(2.5,),
        source_ref="catalog:minimal_kanban/toyota_camry_xv70_a25a",
    ),
    VehicleCatalogEntry(
        key="nissan_xtrail_t32_mr20",
        make_display="Nissan",
        model_display="X-Trail",
        year_from=2014,
        year_to=2021,
        model_aliases=("X-TRAIL", "XTRAIL", "T32"),
        base_fields={
            "generation_or_platform": "T32",
            "fuel_type": "gasoline",
            "wheel_bolt_pattern": "5x114.3",
            "brake_front_type": "ventilated disc",
            "brake_rear_type": "disc",
            "steering_system_type": "electric power steering",
        },
        detailed_fields={
            "engine_code": "MR20DD",
            "engine_model": "MR20DD",
            "engine_displacement_l": 2.0,
            "engine_power_hp": 144,
            "gearbox_type": "CVT",
            "gearbox_model": "JF016E",
            "drivetrain": "AWD",
            "oil_engine_capacity_l": 4.4,
            "oil_gearbox_capacity_l": 7.2,
            "coolant_capacity_l": 7.1,
        },
        engine_codes=("MR20DD",),
        engine_models=("MR20DD",),
        displacements_l=(2.0,),
        source_ref="catalog:minimal_kanban/nissan_xtrail_t32_mr20",
    ),
    VehicleCatalogEntry(
        key="lada_vesta_21129",
        make_display="Lada",
        model_display="Vesta",
        year_from=2016,
        year_to=2024,
        model_aliases=("VESTA",),
        base_fields={
            "generation_or_platform": "2180",
            "fuel_type": "gasoline",
            "wheel_bolt_pattern": "4x100",
            "brake_front_type": "ventilated disc",
            "brake_rear_type": "drum/disc by trim",
            "steering_system_type": "electric power steering",
        },
        detailed_fields={
            "engine_code": "21129",
            "engine_model": "VAZ-21129",
            "engine_displacement_l": 1.6,
            "engine_power_hp": 106,
            "gearbox_type": "manual",
            "drivetrain": "FWD",
            "oil_engine_capacity_l": 4.4,
            "coolant_capacity_l": 7.8,
        },
        engine_codes=("21129", "VAZ-21129"),
        engine_models=("VAZ-21129", "21129"),
        displacements_l=(1.6,),
        source_ref="catalog:minimal_kanban/lada_vesta_21129",
    ),
)


class VehicleProfileService:
    def __init__(self, *, timeout_seconds: float = 12.0) -> None:
        self._timeout_seconds = timeout_seconds

    def normalize_profile_payload(
        self,
        raw_profile: Any,
        *,
        assume_manual_for_explicit_fields: bool = False,
    ) -> tuple[VehicleProfile, set[str], set[str]]:
        raw = raw_profile if isinstance(raw_profile, dict) else {}
        profile = VehicleProfile.from_dict(raw)
        present_primary = {field for field in VEHICLE_PRIMARY_FIELDS if field in raw}
        present_meta = {field for field in VEHICLE_META_FIELDS if field in raw}

        manual_fields = set(profile.manual_fields)
        autofilled_fields = set(profile.autofilled_fields)
        tentative_fields = set(profile.tentative_fields)
        has_meta_hints = any(key in raw for key in ("manual_fields", "autofilled_fields", "tentative_fields"))

        if assume_manual_for_explicit_fields and present_primary and not has_meta_hints:
            manual_fields.update(present_primary)
        elif not manual_fields and not autofilled_fields and present_primary:
            manual_fields.update({field for field in present_primary if raw.get(field) not in (None, "", [])})

        autofilled_fields -= manual_fields
        tentative_fields &= autofilled_fields

        profile.manual_fields = sorted(normalize_vehicle_field_names(list(manual_fields)))
        profile.autofilled_fields = sorted(normalize_vehicle_field_names(list(autofilled_fields)))
        profile.tentative_fields = sorted(normalize_vehicle_field_names(list(tentative_fields)))
        if not profile.manual_fields and not profile.autofilled_fields:
            profile.data_completion_state = "manually_entered"
        return profile, present_primary, present_meta

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
        result.source_confidence = round(max(0.0, min(1.0, result.source_confidence)), 2)
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
        result.autofilled_fields = sorted(normalize_vehicle_field_names(list(autofilled_fields - manual_fields)))
        result.tentative_fields = sorted(normalize_vehicle_field_names(list(tentative_fields)))
        result.data_completion_state = self._derive_completion_state(result)

        if not result.source_summary.strip():
            inferred_summary = self._infer_source_summary(result)
            if inferred_summary:
                result.source_summary = inferred_summary
        if result.source_confidence <= 0:
            result.source_confidence = self._derive_confidence(result)
        else:
            result.source_confidence = round(max(0.0, min(1.0, result.source_confidence)), 2)
        result.warnings = self._normalize_warnings(result.warnings)
        return result

    def autofill_preview(
        self,
        *,
        raw_text: str = "",
        image_base64: str | None = None,
        image_filename: str = "",
        image_mime_type: str = "",
        existing_profile: Any = None,
        explicit_vehicle: str = "",
        explicit_title: str = "",
        explicit_description: str = "",
    ) -> VehicleAutofillResult:
        base_profile = VehicleProfile.from_dict(existing_profile)
        base_profile.raw_input_text = str(raw_text or "").strip()[:6000]
        warnings: list[str] = list(base_profile.warnings)
        used_sources: list[str] = []

        parsed_text_profile, parsed_card_draft, parse_warnings = self._parse_text_payload(
            raw_text,
            explicit_vehicle=explicit_vehicle,
            explicit_title=explicit_title,
            explicit_description=explicit_description,
        )
        for warning in parse_warnings:
            if warning not in warnings:
                warnings.append(warning)

        image_result = self._parse_vehicle_image(
            image_base64=image_base64,
            image_filename=image_filename,
            image_mime_type=image_mime_type,
        )
        for warning in image_result.warnings:
            if warning not in warnings:
                warnings.append(warning)

        merged = deepcopy(base_profile)
        parsed_primary = {
            field_name
            for field_name in VEHICLE_PRIMARY_FIELDS
            if not self._is_empty_vehicle_value(getattr(parsed_text_profile, field_name))
        }
        merged, _ = self.merge_profile_patch(
            merged,
            parsed_text_profile,
            present_primary=parsed_primary,
            present_meta={"raw_input_text"} if parsed_text_profile.raw_input_text else set(),
        )
        if parsed_primary:
            used_sources.append("user_text")

        image_profile = VehicleProfile(raw_image_text=image_result.raw_text, image_parse_status=image_result.status)
        image_primary: set[str] = set()
        if image_result.raw_text:
            image_profile, _, image_parse_warnings = self._parse_text_payload(image_result.raw_text)
            image_profile.raw_image_text = image_result.raw_text
            image_profile.image_parse_status = image_result.status
            image_primary = {
                field_name
                for field_name in VEHICLE_PRIMARY_FIELDS
                if not self._is_empty_vehicle_value(getattr(image_profile, field_name))
            }
            for warning in image_parse_warnings:
                if warning not in warnings:
                    warnings.append(warning)
        if image_primary:
            image_profile.autofilled_fields = sorted(image_primary)
            image_profile.tentative_fields = sorted(image_primary)
            image_profile.field_sources = {field_name: "windows_ocr" for field_name in image_primary}
            image_profile.source_links_or_refs = ["ocr:windows-media-ocr"]
            image_profile.source_summary = "OCR from provided vehicle image"
            image_profile.source_confidence = 0.42
            image_profile.data_completion_state = "partially_autofilled"
            merged = self._merge_autofill_profile(merged, image_profile)
            used_sources.append("vehicle_image_ocr")
        else:
            merged.raw_image_text = image_result.raw_text
            merged.image_parse_status = image_result.status

        catalog_profile = self._enrich_from_catalog(merged)
        if catalog_profile is not None:
            merged = self._merge_autofill_profile(merged, catalog_profile)
            used_sources.append("reference_catalog")

        # Keep preview autofill responsive: if card text + local catalog already
        # identified the vehicle, skip the slower external VIN decode.
        if merged.vin and self._should_enrich_from_vin_decode(base_profile, merged):
            vin_profile = self._enrich_from_vin_decode(merged.vin)
            if vin_profile is not None:
                merged = self._merge_autofill_profile(merged, vin_profile)
                used_sources.append("official_vin_decode")

        merged.raw_input_text = parsed_text_profile.raw_input_text or merged.raw_input_text
        merged.raw_image_text = image_result.raw_text or merged.raw_image_text
        merged.image_parse_status = image_result.status
        merged.warnings = self._normalize_warnings([*warnings, *merged.warnings])
        merged.source_summary = self._build_source_summary(merged, used_sources)
        merged.data_completion_state = self._derive_completion_state(merged)
        merged.source_confidence = self._derive_confidence(merged)

        card_draft = {
            "vehicle": self._suggest_vehicle_label(explicit_vehicle, merged),
            "title": self._suggest_title(explicit_title, raw_text, parsed_card_draft.get("title", ""), merged),
            "description": self._suggest_description(explicit_description, raw_text, parsed_card_draft.get("description", "")),
        }
        return VehicleAutofillResult(
            vehicle_profile=merged,
            card_draft=card_draft,
            warnings=list(merged.warnings),
            used_sources=used_sources,
            image_parse_status=image_result.status,
            image_debug_chunks=image_result.debug_chunks,
        )

    def _should_enrich_from_vin_decode(self, base_profile: VehicleProfile, profile: VehicleProfile) -> bool:
        if any(
            self._is_empty_vehicle_value(getattr(profile, field_name))
            for field_name in ("make_display", "model_display", "production_year")
        ):
            return True
        if base_profile.is_empty():
            return False
        return any(
            self._is_empty_vehicle_value(getattr(profile, field_name))
            for field_name in (
                "engine_code",
                "engine_model",
                "engine_displacement_l",
                "engine_power_hp",
                "gearbox_type",
                "gearbox_model",
                "drivetrain",
                "fuel_type",
            )
        )

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

        detected_make = ""
        for canonical_make, aliases in _MAKE_ALIASES.items():
            if any(re.search(rf"\b{re.escape(alias)}\b", upper_text, re.IGNORECASE) for alias in aliases):
                detected_make = canonical_make
                break
        if detected_make:
            profile.make_display = self._display_make(detected_make)

        model_match = self._detect_model(combined_text, detected_make)
        if model_match:
            profile.model_display = model_match
            generation_or_platform = self._extract_generation_or_platform(combined_text, model_match)
            if generation_or_platform:
                profile.generation_or_platform = generation_or_platform

        engine_code_match = _ENGINE_CODE_PATTERN.search(upper_text)
        if engine_code_match:
            profile.engine_code = normalize_vehicle_text(engine_code_match.group(1), limit=24)

        engine_model_match = _ENGINE_LABEL_PATTERN.search(combined_text)
        if engine_model_match:
            candidate = normalize_vehicle_text(engine_model_match.group(1), limit=40)
            if candidate and candidate.upper() not in {profile.make_display.upper(), profile.model_display.upper()}:
                profile.engine_model = candidate

        displacement_match = _DISPLACEMENT_PATTERN.search(combined_text)
        if displacement_match:
            profile.engine_displacement_l = normalize_vehicle_float(displacement_match.group(1))

        power_match = _POWER_PATTERN.search(upper_text)
        if power_match:
            profile.engine_power_hp = normalize_vehicle_int(power_match.group(1))

        for gearbox_type, pattern in _GEARBOX_PATTERNS:
            if re.search(pattern, upper_text, re.IGNORECASE):
                profile.gearbox_type = gearbox_type
                break
        gearbox_label_match = _GEARBOX_LABEL_PATTERN.search(combined_text)
        if gearbox_label_match:
            candidate = normalize_vehicle_text(gearbox_label_match.group(1), limit=40)
            if candidate:
                profile.gearbox_model = candidate
        elif not profile.gearbox_model:
            gearbox_model_hint = self._extract_gearbox_model_hint(combined_text)
            if gearbox_model_hint:
                profile.gearbox_model = gearbox_model_hint

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

        non_empty_fields = {
            field_name
            for field_name in VEHICLE_PRIMARY_FIELDS
            if not self._is_empty_vehicle_value(getattr(profile, field_name))
        }
        profile.manual_fields = sorted(non_empty_fields)
        profile.data_completion_state = "manually_entered"

        title_candidate = explicit_title.strip()
        description_candidate = explicit_description.strip()
        if not title_candidate:
            title_candidate = self._build_issue_title(combined_text)
        if not description_candidate:
            description_candidate = normalize_text(text, default="", limit=CARD_DESCRIPTION_LIMIT)

        if profile.vin and len(profile.vin) != 17:
            warnings.append("VIN выглядит неполным: автодополнение из интернета может быть ограничено.")

        return profile, {"title": title_candidate, "description": description_candidate}, warnings

    def _parse_vehicle_image(
        self,
        *,
        image_base64: str | None,
        image_filename: str,
        image_mime_type: str,
    ) -> ImageParseResult:
        if not image_base64:
            return ImageParseResult()
        try:
            image_bytes = self._decode_image_base64(image_base64)
        except ValueError as exc:
            return ImageParseResult(status="image_decode_error", warnings=[str(exc)])

        if len(image_bytes) > _VEHICLE_IMAGE_LIMIT_BYTES:
            return ImageParseResult(
                status="image_too_large",
                warnings=[f"Изображение слишком большое для локального OCR: более {_VEHICLE_IMAGE_LIMIT_BYTES // (1024 * 1024)} МБ."],
            )

        suffix = self._image_suffix(image_filename, image_mime_type)
        with tempfile.TemporaryDirectory(prefix="minimal-kanban-vehicle-ocr-") as temp_dir:
            image_path = Path(temp_dir) / f"vehicle-source{suffix}"
            image_path.write_bytes(image_bytes)
            try:
                return self._run_windows_ocr(image_path)
            except Exception as exc:  # pragma: no cover
                return ImageParseResult(
                    status="ocr_failed",
                    warnings=[f"Локальный OCR изображения не удался: {exc}"],
                )

    def _run_windows_ocr(self, image_path: Path) -> ImageParseResult:
        script = r"""
$ErrorActionPreference = 'Stop'
$imagePath = $args[-1]
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop | Out-Null
  $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
  $file = [Windows.Storage.StorageFile]::GetFileFromPathAsync($imagePath).AsTask().GetAwaiter().GetResult()
  $stream = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read).AsTask().GetAwaiter().GetResult()
  $decoder = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream).AsTask().GetAwaiter().GetResult()
  $bitmap = $decoder.GetSoftwareBitmapAsync().AsTask().GetAwaiter().GetResult()
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if ($null -eq $engine) { throw 'Windows OCR engine unavailable.' }
  $result = $engine.RecognizeAsync($bitmap).AsTask().GetAwaiter().GetResult()
  $payload = @{ ok = $true; text = $result.Text; lines = @($result.Lines | ForEach-Object { $_.Text }) }
}
catch {
  $payload = @{ ok = $false; error = $_.Exception.Message }
}
$payload | ConvertTo-Json -Compress -Depth 6
"""
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "-", "--", str(image_path)],
            input=script,
            text=True,
            capture_output=True,
            timeout=max(10, int(self._timeout_seconds)),
            check=False,
        )
        stdout = completed.stdout.strip()
        if not stdout:
            raise RuntimeError("Windows OCR не вернул ответ.")
        payload = json.loads(stdout)
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "unknown OCR error"))
        raw_text = normalize_text(payload.get("text"), default="", limit=6000)
        lines = []
        for raw_line in payload.get("lines", []) if isinstance(payload.get("lines"), list) else []:
            line = normalize_text(raw_line, default="", limit=200)
            if line:
                lines.append(line)
        warnings: list[str] = []
        status = "parsed" if raw_text else "no_text_found"
        if not raw_text:
            warnings.append("На изображении не удалось уверенно распознать текст.")
        return ImageParseResult(raw_text=raw_text, status=status, warnings=warnings, debug_chunks=lines[:20])

    def _enrich_from_vin_decode(self, vin: str) -> VehicleProfile | None:
        normalized_vin = soft_normalize_vin(vin)
        if len(normalized_vin) != 17:
            return None
        url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{normalized_vin}?format=json"
        try:
            response = httpx.get(url, timeout=self._timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None
        results = payload.get("Results")
        if not isinstance(results, list) or not results:
            return None
        record = results[0] if isinstance(results[0], dict) else {}
        profile = VehicleProfile(
            make_display=self._title_case(record.get("Make")),
            model_display=normalize_vehicle_text(record.get("Model")),
            generation_or_platform=self._join_non_empty([record.get("Series"), record.get("Trim")], separator=" / "),
            production_year=normalize_vehicle_int(record.get("ModelYear")),
            vin=normalized_vin,
            engine_code=normalize_vehicle_text(record.get("EngineModel")),
            engine_model=normalize_vehicle_text(record.get("EngineModel")),
            engine_displacement_l=normalize_vehicle_float(record.get("DisplacementL")),
            engine_power_hp=self._kw_or_hp_to_hp(record.get("EngineHP"), record.get("EngineKW")),
            gearbox_type=normalize_vehicle_text(record.get("TransmissionStyle"), limit=40),
            gearbox_model=normalize_vehicle_text(record.get("TransmissionSpeeds"), limit=40),
            drivetrain=normalize_vehicle_text(record.get("DriveType"), limit=40),
            fuel_type=normalize_vehicle_text(record.get("FuelTypePrimary"), limit=40),
            source_summary="Official VIN decode via NHTSA vPIC",
            source_confidence=0.88,
            source_links_or_refs=[url],
            data_completion_state="mostly_autofilled",
            raw_input_text="",
            image_parse_status="not_attempted",
        )
        profile.autofilled_fields = sorted(
            field_name
            for field_name in (
                "make_display",
                "model_display",
                "generation_or_platform",
                "production_year",
                "vin",
                "engine_code",
                "engine_model",
                "engine_displacement_l",
                "engine_power_hp",
                "gearbox_type",
                "gearbox_model",
                "drivetrain",
                "fuel_type",
            )
            if not self._is_empty_vehicle_value(getattr(profile, field_name))
        )
        profile.field_sources = {field_name: "official_vin_decode_nhtsa" for field_name in profile.autofilled_fields}
        return profile if not profile.is_empty() else None

    def _enrich_from_catalog(self, profile: VehicleProfile) -> VehicleProfile | None:
        if not profile.make_display or not profile.model_display:
            return None
        best_entry: VehicleCatalogEntry | None = None
        best_score = 0
        for entry in _CATALOG_ENTRIES:
            score = self._catalog_match_score(entry, profile)
            if score > best_score:
                best_score = score
                best_entry = entry
        if best_entry is None or best_score < 5:
            return None

        candidate = VehicleProfile(
            make_display=best_entry.make_display,
            model_display=best_entry.model_display,
            source_summary=f"Structured reference catalog match ({best_entry.key})",
            source_confidence=0.58 if best_score < 9 else 0.72,
            source_links_or_refs=[best_entry.source_ref],
            data_completion_state="partially_autofilled",
        )
        for field_name, value in best_entry.base_fields.items():
            setattr(candidate, field_name, value)
        if best_score >= 9:
            for field_name, value in best_entry.detailed_fields.items():
                setattr(candidate, field_name, value)
        else:
            candidate.oem_notes = self._join_non_empty(
                [
                    candidate.oem_notes,
                    "Детальные технические параметры требуют подтверждения по двигателю или VIN.",
                ],
                separator=" ",
            )
        candidate.autofilled_fields = sorted(
            field_name for field_name in VEHICLE_PRIMARY_FIELDS if not self._is_empty_vehicle_value(getattr(candidate, field_name))
        )
        candidate.tentative_fields = sorted(
            field_name
            for field_name in candidate.autofilled_fields
            if field_name in {"oil_engine_capacity_l", "oil_gearbox_capacity_l", "coolant_capacity_l", "gearbox_model", "engine_power_hp"}
            and best_score < 9
        )
        candidate.field_sources = {field_name: "structured_reference_catalog" for field_name in candidate.autofilled_fields}
        return candidate if candidate.autofilled_fields else None

    def _merge_autofill_profile(self, base: VehicleProfile, incoming: VehicleProfile) -> VehicleProfile:
        result = deepcopy(base)
        manual_fields = set(result.manual_fields)
        autofilled_fields = set(result.autofilled_fields)
        tentative_fields = set(result.tentative_fields)
        field_sources = dict(result.field_sources)
        source_links = list(result.source_links_or_refs)
        warnings = list(result.warnings)

        for field_name in VEHICLE_PRIMARY_FIELDS:
            next_value = getattr(incoming, field_name)
            if self._is_empty_vehicle_value(next_value):
                continue
            if field_name in manual_fields:
                continue
            if not self._is_empty_vehicle_value(getattr(result, field_name)) and field_name not in autofilled_fields:
                continue
            setattr(result, field_name, deepcopy(next_value))
            autofilled_fields.add(field_name)
            if field_name in incoming.tentative_fields:
                tentative_fields.add(field_name)
            if field_name in incoming.field_sources:
                field_sources[field_name] = incoming.field_sources[field_name]

        for link in incoming.source_links_or_refs:
            if link and link not in source_links:
                source_links.append(link)
        for warning in incoming.warnings:
            if warning not in warnings:
                warnings.append(warning)

        result.source_links_or_refs = source_links[:12]
        result.warnings = warnings
        result.source_summary = self._join_non_empty([result.source_summary, incoming.source_summary], separator="; ")
        result.source_confidence = max(result.source_confidence, incoming.source_confidence)
        result.autofilled_fields = sorted(normalize_vehicle_field_names(list(autofilled_fields - manual_fields)))
        result.tentative_fields = sorted(normalize_vehicle_field_names(list(tentative_fields & set(result.autofilled_fields))))
        result.field_sources = field_sources
        result.data_completion_state = self._derive_completion_state(result)
        if incoming.raw_image_text:
            result.raw_image_text = incoming.raw_image_text
        if incoming.image_parse_status:
            result.image_parse_status = incoming.image_parse_status
        return result

    def _catalog_match_score(self, entry: VehicleCatalogEntry, profile: VehicleProfile) -> int:
        make_slug = self._slug(profile.make_display)
        model_slug = self._slug(profile.model_display)
        if make_slug != self._slug(entry.make_display):
            return 0
        if not any(alias in model_slug for alias in [self._slug(alias) for alias in entry.model_aliases]):
            return 0
        score = 5
        if profile.production_year and entry.year_from <= profile.production_year <= entry.year_to:
            score += 2
        if profile.engine_code and any(self._slug(code) == self._slug(profile.engine_code) for code in entry.engine_codes):
            score += 4
        if profile.engine_model and any(self._slug(model) == self._slug(profile.engine_model) for model in entry.engine_models):
            score += 3
        if profile.engine_displacement_l and any(abs(profile.engine_displacement_l - value) < 0.11 for value in entry.displacements_l):
            score += 3
        return score

    def _decode_image_base64(self, value: str) -> bytes:
        raw = str(value or "").strip()
        if "," in raw and raw.lower().startswith("data:"):
            raw = raw.split(",", 1)[1]
        import base64

        try:
            return base64.b64decode(raw, validate=True)
        except Exception as exc:  # pragma: no cover
            raise ValueError("Не удалось декодировать изображение для OCR.") from exc

    def _image_suffix(self, image_filename: str, image_mime_type: str) -> str:
        suffix = Path(str(image_filename or "")).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".webp"}:
            return suffix
        mime = str(image_mime_type or "").lower()
        return {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/bmp": ".bmp",
            "image/gif": ".gif",
            "image/tiff": ".tiff",
            "image/webp": ".webp",
        }.get(mime, ".png")

    def _extract_capacity(self, pattern: re.Pattern[str], text: str) -> float | None:
        match = pattern.search(text)
        if not match:
            return None
        return normalize_vehicle_float(match.group(1))

    def _kw_or_hp_to_hp(self, hp_value: Any, kw_value: Any) -> int | None:
        hp = normalize_vehicle_int(hp_value)
        if hp:
            return hp
        kw = normalize_vehicle_float(kw_value)
        if not kw:
            return None
        return int(round(kw * 1.34102))

    def _display_make(self, make: str) -> str:
        normalized = normalize_vehicle_text(make)
        if normalized.upper() == "MERCEDES-BENZ":
            return "Mercedes-Benz"
        return normalized.title()

    def _detect_model(self, text: str, detected_make: str) -> str:
        upper_text = text.upper()
        if detected_make == "MAZDA" and re.search(r"\bCX[\s-]?5\b", upper_text, re.IGNORECASE):
            return "CX-5"
        for entry in _CATALOG_ENTRIES:
            if detected_make and self._slug(entry.make_display) != self._slug(detected_make):
                continue
            for alias in entry.model_aliases:
                if re.search(rf"\b{re.escape(alias)}\b", upper_text, re.IGNORECASE):
                    return normalize_vehicle_text(entry.model_display)

        if detected_make:
            make_aliases = _MAKE_ALIASES.get(detected_make, ())
            for alias in make_aliases:
                if alias and alias in upper_text:
                    tail = upper_text.split(alias, 1)[1]
                    tokens = [token for token in _MAKE_MODEL_SPLIT_PATTERN.split(tail) if token]
                    model_tokens: list[str] = []
                    for token in tokens:
                        if _YEAR_PATTERN.fullmatch(token):
                            break
                        if token in {"VIN", "ПРОБЛЕМА", "ЖАЛОБА", "НЕИСПРАВНОСТЬ"}:
                            break
                        model_tokens.append(token)
                        if len(model_tokens) >= 3:
                            break
                    if model_tokens:
                        return self._compose_model_tokens(model_tokens)
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
                return candidate.upper() if re.fullmatch(r"[A-Z0-9\-/. ]+", candidate, re.IGNORECASE) else candidate
        model_tokens = [token for token in re.split(r"[\s\-]+", model_display.upper()) if token]
        if model_tokens:
            joined = r"[\s\-]+".join(re.escape(token) for token in model_tokens)
            near_model = re.search(rf"{joined}\s+([A-Z]{{1,4}}\d{{1,4}}[A-Z]?|[IVX]{{1,5}})\b", text.upper(), re.IGNORECASE)
            if near_model:
                return near_model.group(1).upper()
        platform_tokens = [match.group(1).upper() for match in _PLATFORM_TOKEN_PATTERN.finditer(text.upper())]
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

    def _suggest_vehicle_label(self, explicit_vehicle: str, profile: VehicleProfile) -> str:
        explicit = normalize_text(explicit_vehicle, default="", limit=CARD_VEHICLE_LIMIT)
        if explicit:
            return explicit
        display = profile.display_name()
        return normalize_text(display, default="", limit=CARD_VEHICLE_LIMIT)

    def _suggest_title(self, explicit_title: str, raw_text: str, parsed_title: str, profile: VehicleProfile) -> str:
        explicit = normalize_text(explicit_title, default="", limit=CARD_TITLE_LIMIT)
        if explicit:
            return explicit
        parsed = normalize_text(parsed_title, default="", limit=CARD_TITLE_LIMIT)
        if parsed and parsed != "НОВАЯ КАРТОЧКА ПО АВТО":
            return parsed
        profile_label = profile.display_name()
        if profile_label:
            return f"ДИАГНОСТИКА / {profile_label}".upper()[:CARD_TITLE_LIMIT]
        raw = normalize_text(raw_text, default="", limit=CARD_TITLE_LIMIT)
        return raw.upper() if raw else "НОВАЯ КАРТОЧКА ПО АВТО"

    def _suggest_description(self, explicit_description: str, raw_text: str, parsed_description: str) -> str:
        explicit = normalize_text(explicit_description, default="", limit=CARD_DESCRIPTION_LIMIT)
        if explicit:
            return explicit
        parsed = normalize_text(parsed_description, default="", limit=CARD_DESCRIPTION_LIMIT)
        if parsed:
            return parsed
        return normalize_text(raw_text, default="", limit=CARD_DESCRIPTION_LIMIT)

    def _build_source_summary(self, profile: VehicleProfile, used_sources: list[str]) -> str:
        labels = []
        for source in used_sources:
            if source == "user_text":
                labels.append("user text")
            elif source == "vehicle_image_ocr":
                labels.append("image OCR")
            elif source == "official_vin_decode":
                labels.append("official VIN decode")
            elif source == "reference_catalog":
                labels.append("reference catalog")
        if not labels:
            return profile.source_summary or "manual entry"
        unique_labels: list[str] = []
        for label in labels:
            if label not in unique_labels:
                unique_labels.append(label)
        return ", ".join(unique_labels)

    def _infer_source_summary(self, profile: VehicleProfile) -> str:
        source_values = {str(value or "").strip().lower() for value in profile.field_sources.values()}
        links = [str(value or "").strip().lower() for value in profile.source_links_or_refs]
        if any("official_vin_decode" in value for value in source_values) or any("vpic.nhtsa.dot.gov" in value for value in links):
            return "official VIN decode"
        if any("structured_reference_catalog" in value for value in source_values) or any("catalog:" in value for value in links):
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
        base = profile.source_confidence
        if profile.autofilled_fields:
            base = max(base, 0.48)
            if len(profile.autofilled_fields) >= 6:
                base = max(base, 0.68)
        if profile.manual_fields and not profile.autofilled_fields:
            base = max(base, 0.95)
        return round(max(0.0, min(1.0, base)), 2)

    def _join_non_empty(self, values: list[Any], *, separator: str = " ") -> str:
        normalized = [normalize_vehicle_text(value, limit=80) for value in values]
        normalized = [value for value in normalized if value]
        return separator.join(normalized)

    def _normalize_warnings(self, values: list[str]) -> list[str]:
        warnings: list[str] = []
        for raw in values:
            warning = normalize_vehicle_text(raw, limit=200)
            if warning and warning not in warnings:
                warnings.append(warning)
        return warnings

    def _slug(self, value: str) -> str:
        return re.sub(r"[^a-z0-9а-яё]+", " ", str(value or "").strip().casefold()).strip()

    def _title_case(self, value: Any) -> str:
        text = normalize_vehicle_text(value)
        return text.title() if text else ""

    def _is_empty_vehicle_value(self, value: Any) -> bool:
        return value in (None, "", [], {}, ())
