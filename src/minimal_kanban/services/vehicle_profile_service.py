from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from ..vehicle_profile import (
    VEHICLE_META_FIELDS,
    VEHICLE_PRIMARY_FIELDS,
    VehicleProfile,
    build_vehicle_display,
    normalize_source_confidence,
    normalize_vehicle_field_names,
    normalize_vehicle_int,
    normalize_vehicle_text,
    split_vehicle_display_alias,
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

    def _is_empty_vehicle_value(self, value: Any) -> bool:
        return value in (None, "", [], {}, ())
