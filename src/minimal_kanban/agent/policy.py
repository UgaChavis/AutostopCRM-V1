from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import PatchResult, PlanResult, ToolResult


@dataclass(frozen=True)
class ScenarioPolicy:
    """Lightweight source hints for an intent; never a compulsory workflow."""

    suggested_tools: tuple[str, ...] = ()
    source_type: str = "crm"


_SCENARIO_POLICIES: dict[str, ScenarioPolicy] = {
    "vin_enrichment": ScenarioPolicy(
        suggested_tools=(
            "decode_vin",
            "search_web_multi",
            "fetch_page_excerpt",
            "fetch_page_browser",
        ),
        source_type="external_vin",
    ),
    "full_card_enrichment": ScenarioPolicy(
        suggested_tools=("decode_vin", "find_part_numbers", "lookup_part_prices"),
    ),
    "parts_lookup": ScenarioPolicy(
        suggested_tools=("find_part_numbers", "estimate_price_ru", "lookup_part_prices"),
        source_type="external_parts",
    ),
    "maintenance_lookup": ScenarioPolicy(
        suggested_tools=("estimate_maintenance", "lookup_part_prices"),
        source_type="external_maintenance",
    ),
    "dtc_lookup": ScenarioPolicy(
        suggested_tools=("decode_dtc", "search_fault_info"),
        source_type="external_diagnostic",
    ),
    "fault_research": ScenarioPolicy(
        suggested_tools=("search_fault_info",),
        source_type="external_fault",
    ),
    "normalization": ScenarioPolicy(),
    "repair_order_assistance": ScenarioPolicy(),
    "board_review": ScenarioPolicy(),
    "cash_review": ScenarioPolicy(),
    "freeform_manual": ScenarioPolicy(),
}


_TOOL_SOURCE_TYPES = {
    "decode_vin": "external_vin",
    "find_part_numbers": "external_parts",
    "search_part_numbers": "external_parts",
    "estimate_price_ru": "external_price",
    "lookup_part_prices": "external_price",
    "decode_dtc": "external_diagnostic",
    "search_fault_info": "external_fault",
    "estimate_maintenance": "external_maintenance",
    "search_web_multi": "external_search_multi",
    "search_web": "external_search",
    "fetch_page_excerpt": "external_page",
    "fetch_page_browser": "external_page_browser",
    "research_drive2_cases": "external_drive2_case_research",
    "get_store_quote_part_context": "store_context",
    "update_card": "crm_write",
    "update_repair_order": "crm_write",
    "replace_repair_order_works": "crm_write",
    "replace_repair_order_materials": "crm_write",
    "set_repair_order_status": "crm_write",
    "mark_card_ready": "crm_write",
    "create_card": "crm_write",
    "move_card": "crm_write",
    "archive_card": "crm_write",
    "restore_card": "crm_write",
    "create_cashbox": "crm_write",
    "delete_cashbox": "crm_write",
    "create_cash_transaction": "crm_write",
}


class ToolPolicyEngine:
    """Expose useful capabilities without deciding the agent's route or answer."""

    def build_plan(
        self,
        *,
        scenario_chain: list[str],
        execution_mode: str,
        followup_enabled: bool,
        notes: list[str] | None = None,
    ) -> PlanResult:
        normalized_execution_mode = (
            str(execution_mode or "model_loop").strip().lower() or "model_loop"
        )
        normalized_chain = self._normalize_chain(scenario_chain)
        recognized_chain = [item for item in normalized_chain if item in _SCENARIO_POLICIES] or [
            "freeform_manual"
        ]
        primary = next(
            (item for item in recognized_chain if item not in {"normalization", "freeform_manual"}),
            recognized_chain[0],
        )
        suggested_tools = self._unique(
            [
                tool_name
                for scenario_name in recognized_chain
                for tool_name in self._policy_for(scenario_name).suggested_tools
            ]
        )
        return PlanResult(
            scenario_id=primary,
            scenario_chain=recognized_chain,
            execution_mode=normalized_execution_mode,
            needs_external_tools=bool(suggested_tools),
            required_tools=[],
            optional_tools=suggested_tools,
            tool_order=[],
            allowed_write_targets=[],
            forbidden_write_targets=[],
            stop_conditions=[],
            followup_policy={
                "enabled": bool(followup_enabled),
                "owner": "card_service" if followup_enabled else "",
                "mode": "adaptive_followup" if followup_enabled else "none",
            },
            confidence_mode="evidence_guided" if suggested_tools else "standard",
            write_mode="patch_only",
            notes=list(notes or []),
        )

    def missing_required_tools(self, plan: PlanResult, tool_results: list[ToolResult]) -> list[str]:
        """Compatibility hook: intent hints never block a useful final answer."""

        return []

    def filter_patch(self, plan: PlanResult, patch: PatchResult) -> PatchResult:
        """Normalize an untrusted patch without limiting it to a scenario label."""

        return PatchResult(**patch.to_dict())

    def tool_source_type(self, tool_name: str, *, scenario_id: str | None = None) -> str:
        normalized_tool = str(tool_name or "").strip().lower()
        if normalized_tool in _TOOL_SOURCE_TYPES:
            return _TOOL_SOURCE_TYPES[normalized_tool]
        if scenario_id:
            return self._policy_for(scenario_id).source_type
        return "crm"

    def policy_for_scenario(self, scenario_name: str) -> dict[str, Any]:
        policy = self._policy_for(scenario_name)
        return {
            "required_tools": [],
            "optional_tools": list(policy.suggested_tools),
            "allowed_write_targets": [],
            "forbidden_write_targets": [],
            "source_type": policy.source_type,
        }

    def _policy_for(self, scenario_name: str) -> ScenarioPolicy:
        normalized_name = str(scenario_name or "").strip().lower()
        return _SCENARIO_POLICIES.get(normalized_name, _SCENARIO_POLICIES["freeform_manual"])

    @staticmethod
    def _normalize_chain(scenario_chain: list[str]) -> list[str]:
        return ToolPolicyEngine._unique(scenario_chain)

    @staticmethod
    def _unique(items: list[str] | tuple[str, ...]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            value = str(item or "").strip().lower()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result
