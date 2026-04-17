from __future__ import annotations

import subprocess
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.graphics import renderPDF


ROOT = Path(__file__).resolve().parents[1]
MASTER_PLAN = ROOT / "MASTER-PLAN.md"
OUTPUT = ROOT / "MASTER-PLAN.pdf"

MODULE_ROWS = [
    ("1", "Platform Runtime", "v1.2", "stable", "main.py, main_mcp.py, main_agent.py, app.py"),
    ("2", "Board Core", "v1.5", "stable", "card_service.py, column_service.py"),
    ("3", "Workshop Operations", "v1.4", "active", "vehicle_profile.py, repair_order.py, printing/service.py"),
    ("4", "API and Access Control", "v1.4", "stable", "api/server.py, operator_auth.py"),
    ("5", "MCP Layer", "v1.4", "hardening", "mcp/server.py, mcp/client.py, mcp/runtime.py"),
    ("6", "Server AI Contour", "v1.6", "active", "agent/control.py, runner.py, policy.py"),
    ("7", "Browser UI Surface", "v1.5", "active", "web_assets.py"),
    ("8", "Tests and Diagnostics", "v1.4", "stable", "tests/*, check_live_connector.py"),
    ("9", "Docs and Handoff", "v1.2", "active", "00_START_HERE, PROJECT_HANDOFF, MASTER-PLAN"),
]

LANE_ROWS = [
    ("Lane A", "Server AI", "Module 6", "VIN fallback, scenario quality, follow-up tuning"),
    ("Lane B", "MCP and transport", "Module 5", "runtime stability, connector behavior"),
    ("Lane C", "Browser UI", "Module 7", "DnD polish, employees UX, browser recheck"),
    ("Lane D", "Core CRM", "Modules 2-4", "repair orders, employees, board state integrity"),
    ("Lane E", "Ops and docs", "Modules 1,8,9", "production parity, smoke checks, handoff docs"),
]


def _register_font() -> str:
    candidates = [
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            pdfmetrics.registerFont(TTFont("AutoStopSans", str(candidate)))
            return "AutoStopSans"
    return "Helvetica"


FONT_NAME = _register_font()


def _git_output(args: list[str]) -> str:
    try:
        return subprocess.check_output(args, cwd=ROOT, text=True, encoding="utf-8").strip()
    except Exception:
        return "unknown"


def _read_markdown_lines() -> list[str]:
    return MASTER_PLAN.read_text(encoding="utf-8", errors="replace").splitlines()


def _make_styles():
    base = getSampleStyleSheet()
    base["Normal"].fontName = FONT_NAME
    base["Title"].fontName = FONT_NAME
    base["Heading1"].fontName = FONT_NAME
    base["Heading2"].fontName = FONT_NAME
    base["Heading3"].fontName = FONT_NAME
    styles = {
        "title": ParagraphStyle(
            "AutoStopTitle",
            parent=base["Title"],
            fontName=FONT_NAME,
            fontSize=24,
            leading=29,
            textColor=colors.HexColor("#203226"),
            spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "AutoStopSubtitle",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=11,
            leading=15,
            textColor=colors.HexColor("#4c5b4f"),
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "AutoStopBody",
            parent=base["Normal"],
            fontName=FONT_NAME,
            fontSize=9.5,
            leading=13,
            spaceAfter=4,
        ),
        "h1": ParagraphStyle(
            "AutoStopH1",
            parent=base["Heading1"],
            fontName=FONT_NAME,
            fontSize=15,
            leading=19,
            textColor=colors.HexColor("#27402e"),
            spaceBefore=8,
            spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "AutoStopH2",
            parent=base["Heading2"],
            fontName=FONT_NAME,
            fontSize=11.5,
            leading=15,
            textColor=colors.HexColor("#36503d"),
            spaceBefore=6,
            spaceAfter=4,
        ),
        "mono": ParagraphStyle(
            "AutoStopMono",
            parent=base["Code"],
            fontName=FONT_NAME,
            fontSize=8.5,
            leading=11,
            backColor=colors.HexColor("#f3f5ef"),
            borderPadding=4,
            spaceAfter=4,
        ),
    }
    return styles


def _architecture_diagram() -> Drawing:
    drawing = Drawing(520, 170)
    box_fill = colors.HexColor("#edf4e7")
    box_edge = colors.HexColor("#6d7d68")
    title_fill = colors.HexColor("#27402e")

    def box(x: int, y: int, w: int, h: int, title: str, subtitle: str) -> None:
        drawing.add(Rect(x, y, w, h, rx=8, ry=8, fillColor=box_fill, strokeColor=box_edge, strokeWidth=1))
        drawing.add(Rect(x, y + h - 20, w, 20, rx=8, ry=8, fillColor=title_fill, strokeColor=title_fill))
        drawing.add(String(x + 8, y + h - 14, title, fontName=FONT_NAME, fontSize=10, fillColor=colors.white))
        for idx, part in enumerate(subtitle.split("\n")):
            drawing.add(String(x + 8, y + h - 36 - idx * 12, part, fontName=FONT_NAME, fontSize=8.5, fillColor=colors.HexColor("#203226")))

    box(10, 88, 155, 64, "UI Surface", "Desktop UI\nBrowser UI")
    box(185, 88, 155, 64, "Service Core", "Local API\nCardService")
    box(360, 88, 150, 64, "Persistence", "JsonStore\nState files")
    box(95, 10, 155, 56, "MCP Layer", "MCP runtime\nBoardApiClient")
    box(285, 10, 170, 56, "Server AI", "AgentControl\nRunner / Policy / Tools")

    def arrow(x1: int, y1: int, x2: int, y2: int) -> None:
        drawing.add(Line(x1, y1, x2, y2, strokeColor=colors.HexColor("#556657"), strokeWidth=1.2))
        drawing.add(Line(x2, y2, x2 - 6, y2 + 3, strokeColor=colors.HexColor("#556657"), strokeWidth=1.2))
        drawing.add(Line(x2, y2, x2 - 6, y2 - 3, strokeColor=colors.HexColor("#556657"), strokeWidth=1.2))

    arrow(165, 120, 185, 120)
    arrow(340, 120, 360, 120)
    arrow(175, 54, 185, 88)
    arrow(340, 54, 320, 88)
    return drawing


def _module_tree_diagram() -> Drawing:
    drawing = Drawing(520, 250)
    fill = colors.HexColor("#f6f8f2")
    edge = colors.HexColor("#7c8a76")
    text = colors.HexColor("#243629")

    nodes = [
        (190, 214, 150, 24, "0. Product Envelope v1.6"),
        (20, 164, 120, 22, "1. Runtime v1.2"),
        (150, 164, 120, 22, "2. Board v1.5"),
        (280, 164, 120, 22, "3. Workshop v1.4"),
        (410, 164, 100, 22, "4. API v1.4"),
        (20, 110, 120, 22, "5. MCP v1.4"),
        (150, 110, 120, 22, "6. AI v1.6"),
        (280, 110, 120, 22, "7. UI v1.5"),
        (410, 110, 100, 22, "8. Tests v1.4"),
        (190, 56, 150, 22, "9. Docs v1.2"),
    ]

    for x, y, w, h, label in nodes:
        drawing.add(Rect(x, y, w, h, rx=6, ry=6, fillColor=fill, strokeColor=edge, strokeWidth=1))
        drawing.add(String(x + 7, y + 7, label, fontName=FONT_NAME, fontSize=8.5, fillColor=text))

    def connect(x1: int, y1: int, x2: int, y2: int) -> None:
        drawing.add(Line(x1, y1, x2, y2, strokeColor=edge, strokeWidth=1))

    root_x = 265
    for mid_x in (80, 210, 340, 460):
        connect(root_x, 214, mid_x, 186)
    for mid_x in (80, 210, 340, 460):
        connect(root_x, 78, mid_x, 110)
    return drawing


def _build_story():
    styles = _make_styles()
    branch = _git_output(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    head = _git_output(["git", "rev-parse", "--short", "HEAD"])

    story = [
        Paragraph("MASTER PLAN: AutoStop CRM", styles["title"]),
        Paragraph(
            f"Центральный архитектурный план ветки <b>{branch}</b>. Текущий локальный HEAD при генерации PDF: <b>{head}</b>.",
            styles["subtitle"],
        ),
        Paragraph(
            "Документ задает рабочую карту проекта, дерево модулей, их внутренние версии, текущий конструктив AI-контура и параллельные дорожки разработки.",
            styles["body"],
        ),
        Spacer(1, 4 * mm),
        Paragraph("1. Контур системы", styles["h1"]),
        Paragraph(
            "Система построена как набор слоев: UI -> local API -> CardService -> storage, с отдельными MCP и AI-контурами поверх того же бизнес-ядра.",
            styles["body"],
        ),
    ]

    story.append(_architecture_diagram())
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("2. Дерево модулей и версии", styles["h1"]))
    story.append(
        Paragraph(
            "Ниже используется внутренняя модульная версия зрелости. Она нужна для параллельной работы, а не как public release numbering.",
            styles["body"],
        )
    )

    table_data = [["ID", "Модуль", "Версия", "Статус", "Ключевые файлы"], *MODULE_ROWS]
    table = Table(table_data, colWidths=[22 * mm, 42 * mm, 18 * mm, 24 * mm, 78 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#27402e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                ("FONTSIZE", (0, 0), (-1, -1), 8.2),
                ("LEADING", (0, 0), (-1, -1), 10),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#90a08b")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f7f9f4")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f7f9f4"), colors.HexColor("#eef4e9")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 4 * mm))
    story.append(_module_tree_diagram())
    story.append(PageBreak())

    story.append(Paragraph("3. Главный AI-контур", styles["h1"]))
    for item in [
        "AI-контур сейчас живет в Module 6: Server AI Contour v1.6.",
        "Основной контракт: read -> evidence -> plan -> tools -> patch -> write -> verify.",
        "Control layer нормализует scheduler/status edge cases и аккуратнее показывает last_scheduler_error и recent runs.",
        "Policy layer фильтрует unknown scenarios, нормализует execution mode, required tools и forbidden write targets.",
        "Automotive lookup layer использует per-run cache для повторных VIN, parts и price запросов.",
        "Следующий вероятный шаг по качеству: второй VIN source или более сильный fallback для sparse VIN decode.",
    ]:
        story.append(Paragraph(f"- {item}", styles["body"]))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("4. Параллельные дорожки", styles["h1"]))
    lane_table = Table([["Lane", "Зона", "Модули", "Основной фокус"], *LANE_ROWS], colWidths=[20 * mm, 38 * mm, 28 * mm, 86 * mm], repeatRows=1)
    lane_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#36503d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                ("FONTSIZE", (0, 0), (-1, -1), 8.2),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#90a08b")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f7f9f4"), colors.HexColor("#eef4e9")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(lane_table)

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("5. Правило обновления", styles["h1"]))
    for item in [
        "После каждого заметного development-pass обновлять MASTER-PLAN.md и пересобирать MASTER-PLAN.pdf.",
        "Если меняется зрелость модуля, увеличивать только версию соответствующего блока, а не переписывать все версии подряд.",
        "Перед работой с production всегда отдельно подтверждать parity local / GitHub / server.",
    ]:
        story.append(Paragraph(f"- {item}", styles["body"]))

    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Файлы-ориентиры: MASTER-PLAN.md, 00_START_HERE_AUTOSTOP_CRM.md, PROJECT_HANDOFF.md.", styles["subtitle"]))
    return story


def _draw_page_frame(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#90a08b"))
    canvas.setLineWidth(0.5)
    canvas.rect(12 * mm, 12 * mm, A4[0] - 24 * mm, A4[1] - 24 * mm)
    canvas.setFont(FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor("#4c5b4f"))
    canvas.drawRightString(A4[0] - 16 * mm, 10 * mm, f"AutoStop CRM Master Plan · {doc.page}")
    canvas.restoreState()


def main() -> None:
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="AutoStop CRM Master Plan",
    )
    doc.build(_build_story(), onFirstPage=_draw_page_frame, onLaterPages=_draw_page_frame)
    print(OUTPUT)


if __name__ == "__main__":
    main()
