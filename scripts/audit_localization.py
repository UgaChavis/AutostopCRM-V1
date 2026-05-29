from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGETS = [
    ROOT / "src" / "minimal_kanban" / "app.py",
    ROOT / "src" / "minimal_kanban" / "services" / "card_service.py",
    ROOT / "src" / "minimal_kanban" / "services" / "card_service_payroll.py",
    ROOT / "src" / "minimal_kanban" / "ui" / "dialogs.py",
    ROOT / "src" / "minimal_kanban" / "ui" / "main_window.py",
    ROOT / "src" / "minimal_kanban" / "ui" / "widgets.py",
    ROOT / "README.md",
    ROOT / "API_GUIDE.md",
    ROOT / "MCP_GUIDE.md",
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


def main() -> int:
    problems: list[str] = []
    for path in TARGETS:
        if not path.exists():
            problems.append(f"Отсутствует файл для проверки локализации: {path}")
            continue
        content = path.read_text(encoding="utf-8")
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
