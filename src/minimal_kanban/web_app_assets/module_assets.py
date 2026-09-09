"""Source expansion and content-addressed optional browser modules."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from importlib import resources


def _read_source_chunk(name: str) -> str:
    return resources.files(__package__).joinpath("source", name).read_text(encoding="utf-8")


_LAZY_GROUPS = {
    "payroll": ("employees_markup.js", "payroll_workspace.js", "employees_mobile.js"),
    "inventory": ("inventory_workspace.js",),
    "cash_journal": ("cash_journal.js",),
    "auxiliary": (
        "display_dashboard_workspace.js",
        "shared_files_workspace.js",
        "mobile_auxiliary_workspace.js",
    ),
}
_PASSIVE_MODULE_FUNCTIONS = {
    "clearDisplayDashboardImageDrafts",
    "syncEmployeesReadOnlyWorkspaceUi",
    "renderMobileEmployeesPanel",
    "renderMobileInventory",
    "renderMobileArchivePanel",
    "renderMobileSharedFilesPanel",
    "confirmDiscardEmployeeChanges",
    "handleSharedFilesPaste",
    "hideSharedFilesContextMenu",
    "handleSharedFilesDocumentClick",
    "handleSharedFilesGlobalKeydown",
}

_AUXILIARY_SHARED_NAMES = (
    "ATTACHMENT_MIME_TO_EXTENSION",
    "DISPLAY_DASHBOARD_MAX_IMAGES",
    "SHARED_FILE_UPLOAD_MAX_SIZE_BYTES",
    "applyArchivedCardPatch",
    "archivedCardsTotal",
    "arrayBufferToBase64",
    "attachmentExtension",
    "attachmentMimeTypeFromExtension",
    "captureViewerRequestContext",
    "cardHeading",
    "clipboardAttachmentName",
    "columnLabelById",
    "downloadAttachment",
    "escapeHtml",
    "filteredArchiveCards",
    "finiteNonNegativeNumber",
    "finiteNumber",
    "formatBytes",
    "formatDate",
    "isModalOpen",
    "loadArchive",
    "maybeOpenModal",
    "normalizeAttachmentMimeType",
    "openMobileCardDetail",
    "popModal",
    "pushModal",
    "refreshSnapshot",
    "renderMobileMore",
    "renderMobileMoreModules",
    "requireOperatorSession",
    "setMobileView",
    "stripDescriptionFormatting",
    "syncMobileMorePanelChrome",
    "withAccessToken",
)


def _function_names(source: str) -> list[str]:
    return re.findall(r"^    (?:async )?function ([A-Za-z_$][\w$]*)\(", source, re.MULTILINE)


def _function_chunk(source: str, name: str) -> str:
    match = re.search(
        rf"^    (?:async )?function {re.escape(name)}\(.*?^    }}\n",
        source,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise RuntimeError(f"Missing board helper: {name}")
    return match.group()


_CASH_JOURNAL_CORE = _function_chunk(_read_source_chunk("cash_journal.js"), "cashJournalLinkFlags")


def _module_proxies(group: str, source: str, public_names: set[str]) -> str:
    return "\n".join(
        f"    function {name}(...args) {{ return invokeBoardModule({json.dumps(group)}, {json.dumps(name)}, args, {str(name.startswith('close') or name in _PASSIVE_MODULE_FUNCTIONS).lower()}); }}"
        for name in _function_names(source)
        if name != "cashJournalLinkFlags" and name in public_names
    )


def read_board_source(name: str, *, proxies: dict[str, str] | None = None) -> str:
    source = _read_source_chunk(name)
    if proxies is not None and name in proxies:
        eager = "\n".join(
            read_board_source(child)
            for child in re.findall(r"^    // @include ([a-z_]+\.js)$", source, flags=re.MULTILINE)
        )
        return eager + "\n" + proxies[name]
    return re.sub(
        r"^    // @include ([a-z_]+\.js)$",
        lambda match: read_board_source(match.group(1), proxies=proxies),
        source,
        flags=re.MULTILINE,
    )


def _module_script(group: str, source: str, public_names: set[str]) -> str:
    if group == "cash_journal":
        source = source.replace(_CASH_JOURNAL_CORE, "")
    if group == "printing":
        source = source.replace(
            "printRepairOrderDraft = function() { return openRepairOrderPrintWorkspace(); };",
            "function printRepairOrderDraft() { return openRepairOrderPrintWorkspace(); }",
        )
    exports = [name for name in _function_names(source) if name in public_names]
    reset = ""
    if group == "printing":
        reset = """
    const initialPrintState = JSON.parse(JSON.stringify(repairOrderPrintState));
    function resetViewer() {
      resetPrintAsyncContext();
      [printTemplatePreviewTimer, manualPrintPreviewTimer, regulatedPrintPreviewTimer, completionActPreviewTimer].forEach((timer) => window.clearTimeout(timer));
      Object.assign(repairOrderPrintState, JSON.parse(JSON.stringify(initialPrintState)));
      [printEls.modal, printEls.templateModal, printEls.inspectionSheetModal, printEls.completionActModal].forEach((modal) => modal?.classList.remove('is-open'));
    }
"""
        exports.append("resetViewer")
    shared_context = ""
    if group == "auxiliary":
        shared_context = (
            "    const {\n"
            + "".join(f"      {name},\n" for name in _AUXILIARY_SHARED_NAMES)
            + "    } = context.shared;\n"
        )
    return (
        f"window.registerBoardModule({json.dumps(group)}, function(context) {{\n"
        "    const {state, els, api, setStatus} = context;\n"
        + shared_context
        + source
        + reset
        + "\n    return {"
        + ", ".join(exports)
        + "};\n});\n"
    )


def build_board_module_assets(
    contract_text: str, printing_script: str, fingerprint: Callable[[str, str], str]
) -> tuple[str, dict[str, str], dict[str, str]]:
    public_names = {}
    for group, sources in _LAZY_GROUPS.items():
        outside = contract_text
        for name in sources:
            outside = outside.replace(read_board_source(name), "")
        outside_identifiers = set(re.findall(r"\b[A-Za-z_$][\w$]*\b", outside))
        public_names[group] = {
            name
            for source_name in sources
            for name in _function_names(_read_source_chunk(source_name))
            if name in outside_identifiers
        }
    module_sources = {
        group: _module_script(
            group,
            "\n".join(_read_source_chunk(name) for name in sources),
            public_names[group],
        )
        for group, sources in _LAZY_GROUPS.items()
    }
    module_sources["printing"] = _module_script(
        "printing", printing_script, {"printRepairOrderDraft"}
    )
    modules = {fingerprint("js", source): source for source in module_sources.values()}
    manifest = {group: fingerprint("js", source) for group, source in module_sources.items()}
    proxies = {
        name: _module_proxies(group, _read_source_chunk(name), public_names[group])
        for group, sources in _LAZY_GROUPS.items()
        for name in sources
    }
    runtime_document = (
        contract_text.replace(
            read_board_source("app_main_before_printing.js"),
            read_board_source("app_main_before_printing.js", proxies=proxies),
        )
        .replace(
            _read_source_chunk("cash_journal.js"),
            _CASH_JOURNAL_CORE + proxies["cash_journal.js"],
        )
        .replace(
            printing_script,
            "    function printRepairOrderDraft(...args) { return invokeBoardModule('printing', 'printRepairOrderDraft', args); }\n",
        )
        .replace(
            "  <script>\n",
            "  <script>\n    const BOARD_MODULE_MANIFEST = "
            + json.dumps(manifest, separators=(",", ":"))
            + ";\n"
            + _read_source_chunk("module_loader.js"),
            1,
        )
    )

    return runtime_document, modules, manifest
