from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "src" / "minimal_kanban" / "web_app_assets" / "source" / "app_main_before_printing.js"
)


def test_card_description_save_does_not_trim_editor_payload() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "return String(els.cardDescription?.value || '').trim();" not in source
    assert "return String(els.cardDescription?.value || '');" in source
    assert ".replace(/[ \\t]+\\n/g, '\\n')" not in source
    assert "description: String(values.description ?? card.description ?? '').trim()," not in source
    assert "description: String(values.description ?? card.description ?? '')," in source
