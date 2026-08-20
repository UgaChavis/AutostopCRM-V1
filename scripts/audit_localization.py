from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALIZATION_AUDIT_TEXT_MAX_BYTES = 2 * 1024 * 1024

TARGETS = [
    ROOT / "src" / "minimal_kanban" / "app.py",
    ROOT / "src" / "minimal_kanban" / "services" / "card_service.py",
    ROOT / "src" / "minimal_kanban" / "services" / "card_service_payroll.py",
    ROOT / "src" / "minimal_kanban" / "ui" / "main_window.py",
    ROOT / "src" / "minimal_kanban" / "ui" / "widgets.py",
    ROOT / "README.md",
    ROOT / "API_GUIDE.md",
    ROOT / "MCP_GUIDE.md",
    ROOT / "CHATGPT_CONNECTOR_SETUP.md",
    ROOT / "docs" / "OPERATIONS_RUNBOOK.md",
]

FORBIDDEN_PHRASES = [
    "Quick Start",
    "Create Card",
    "Edit Card",
    "New Card",
    "Help",
    "Start",
    "Pause",
    "Reset",
    "Archive",
    "No description",
    "Startup Error",
    "Unexpected Error",
]

ALLOWED_EXCERPTS = [
    "Start Kanban.exe",
    "Start%20Kanban.exe",
    "AuditArchiveStore",
]

FORBIDDEN_MOJIBAKE_FRAGMENTS = [
    "РќСѓР¶РЅРѕ",
    "РЎРѕС‚СЂСѓРґРЅРёРє",
    "РѕР±РЅРѕРІРёР»",
    "РїРµСЂРµРјРµСЃС‚РёР»",
    "СѓРґР°Р»РёР»",
]


def _read_text(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.read(LOCALIZATION_AUDIT_TEXT_MAX_BYTES + 1)
    if len(raw) > LOCALIZATION_AUDIT_TEXT_MAX_BYTES:
        raise ValueError(f"localization audit file is too large: {path}")
    return raw.decode("utf-8")


def main() -> int:
    problems: list[str] = []
    for path in TARGETS:
        if not path.exists():
            problems.append(f"Отсутствует файл для проверки локализации: {path}")
            continue
        try:
            content = _read_text(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            problems.append(f"{path}: не удалось прочитать файл: {exc}")
            continue
        sanitized = content
        for excerpt in ALLOWED_EXCERPTS:
            sanitized = sanitized.replace(excerpt, "")
        for phrase in FORBIDDEN_PHRASES:
            if phrase in sanitized:
                problems.append(f"{path}: найдена запрещённая строка `{phrase}`")
        for fragment in FORBIDDEN_MOJIBAKE_FRAGMENTS:
            if fragment in sanitized:
                problems.append(f"{path}: найдена строка с битой кодировкой `{fragment}`")

    if problems:
        print("\n".join(problems))
        return 1

    print(
        "Проверка локализации завершена успешно: остаточных пользовательских английских строк не найдено."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
