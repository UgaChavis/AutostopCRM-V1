"""Built-in document definitions and templates for the AutoStop CRM print module.

To add a new printable document:
1. Register a new ``PrintDocumentDefinition`` in ``BUILTIN_PRINT_DOCUMENTS``.
2. Add at least one built-in template record in ``builtin_template_records()`` with
   the matching ``document_type``.
3. Reuse the shared placeholders produced by ``PrintModuleService._build_document_context``.
4. For manual multi-page preview splitting, insert ``<!-- AUTOSTOPCRM_PAGE_BREAK -->``
   between template sections. The preview layer will treat each marker as a new page.
"""

from __future__ import annotations

from .models import PrintDocumentDefinition, PrintTemplateRecord

BUILTIN_PRINT_DOCUMENTS: tuple[PrintDocumentDefinition, ...] = (
    PrintDocumentDefinition(
        id="repair_order",
        label="Заказ-наряд",
        description="Основной документ по ремонту, работам и материалам.",
        default_template_id="builtin:repair_order:standard",
    ),
    PrintDocumentDefinition(
        id="invoice",
        label="Счет на оплату",
        description="Печатная форма счета с итоговой суммой по заказу.",
        default_template_id="builtin:invoice:standard",
    ),
    PrintDocumentDefinition(
        id="invoice_factura",
        label="Счет-фактура",
        description="Документ с реквизитами, строками работ и материалов.",
        default_template_id="builtin:invoice_factura:standard",
    ),
    PrintDocumentDefinition(
        id="inspection_sheet",
        label="Дефектовочная ведомость",
        description="Фиксация обращения, выявленных дефектов и рекомендаций.",
        default_template_id="builtin:inspection_sheet:standard",
    ),
    PrintDocumentDefinition(
        id="completion_act",
        label="Акт выполненных работ",
        description="Подтверждение выполненных работ и итоговой стоимости.",
        default_template_id="builtin:completion_act:standard",
    ),
)


PRINT_BASE_STYLES = """
  :root {
    color-scheme: light;
    --paper-text: #171717;
    --paper-soft: #5a5a5a;
    --paper-line: #cfcfcf;
    --paper-line-strong: #8d8d8d;
    --paper-accent: #202020;
    --paper-bg: #ffffff;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    padding: 0;
    background: #edf0f3;
    color: var(--paper-text);
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 12px;
    line-height: 1.45;
  }
  body { padding: 16px; }
  .document-shell { max-width: 920px; margin: 0 auto; }
  .document-page {
    background: var(--paper-bg);
    width: 210mm;
    min-height: 297mm;
    margin: 0 auto 18px;
    padding: 12mm 12mm 13mm;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08);
    border: 1px solid rgba(0, 0, 0, 0.05);
    page-break-after: always;
  }
  .document-page:last-child { page-break-after: auto; }
  .doc-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(260px, 330px);
    gap: 18px;
    align-items: start;
    margin-bottom: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid rgba(0, 0, 0, 0.09);
  }
  .doc-head__brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
  .doc-brand-mark {
    flex: 0 0 auto;
    width: 96px;
    height: 96px;
    border-radius: 20px;
    border: 1px solid rgba(0, 0, 0, 0.08);
    background: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 9px;
  }
  .doc-brand-mark img { display: block; width: 100%; height: 100%; object-fit: contain; }
  .doc-brand-mark__fallback {
    width: 100%;
    height: 100%;
    border-radius: 999px;
    border: 5px solid #e31d1a;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    font-weight: 700;
    color: var(--paper-accent);
    text-transform: uppercase;
  }
  .doc-brand-copy { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .doc-kicker { color: var(--paper-soft); font-size: 10px; text-transform: uppercase; letter-spacing: 0.14em; }
  .doc-title { margin: 0; font-size: 24px; line-height: 1.05; font-weight: 700; letter-spacing: 0.01em; }
  .doc-subtitle { color: var(--paper-soft); font-size: 12px; }
  .doc-service { text-align: right; max-width: 330px; }
  .doc-service__name { font-weight: 700; font-size: 14px; margin-bottom: 4px; }
  .doc-service__meta { color: var(--paper-soft); white-space: pre-wrap; }
  .doc-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
  .doc-card { border: 1px solid var(--paper-line); border-radius: 9px; padding: 8px 10px; min-height: 60px; background: rgba(255,255,255,0.65); }
  .doc-card--wide { grid-column: span 3; }
  .doc-label { color: var(--paper-soft); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px; }
  .doc-value { white-space: pre-wrap; word-break: break-word; }
  .doc-section { margin-bottom: 12px; }
  .doc-section__title { margin: 0 0 7px; font-size: 14px; font-weight: 700; }
  .doc-note { border: 1px solid var(--paper-line); border-radius: 9px; padding: 10px 12px; min-height: 64px; white-space: normal; line-height: 1.55; background: #fcfcfc; }
  .doc-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  .doc-table th, .doc-table td { border: 1px solid var(--paper-line); padding: 7px 8px; vertical-align: top; text-align: left; }
  .doc-table th { font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--paper-accent); background: rgba(0, 0, 0, 0.025); }
  .doc-table__narrow { width: 12%; text-align: right !important; font-variant-numeric: tabular-nums; }
  .doc-table__sum { width: 16%; text-align: right !important; font-variant-numeric: tabular-nums; }
  .doc-table__empty { color: var(--paper-soft); text-align: center; }
  .doc-table tfoot td { font-weight: 700; }
  .doc-totals { margin-top: 12px; margin-left: auto; width: min(380px, 100%); border: 1px solid var(--paper-line-strong); border-radius: 10px; overflow: hidden; }
  .doc-totals__row { display: flex; justify-content: space-between; gap: 12px; padding: 9px 12px; border-bottom: 1px solid var(--paper-line); font-variant-numeric: tabular-nums; }
  .doc-totals__row:last-child { border-bottom: 0; }
  .doc-totals__row--grand { font-size: 15px; font-weight: 700; background: rgba(0, 0, 0, 0.03); }
  .doc-list { margin: 0; padding-left: 18px; }
  .doc-list li + li { margin-top: 4px; }
  .doc-signatures { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin-top: 24px; }
  .doc-signatures__item { border-top: 1px solid var(--paper-line-strong); padding-top: 8px; min-height: 36px; }
  .doc-hint { color: var(--paper-soft); font-size: 11px; }
  .doc-page-break { display: block; height: 0; page-break-after: always; }
  @page { size: A4; margin: 10mm; }
""".strip()


_BUILTIN_CREATED_AT = "2026-04-06T00:00:00+00:00"


def _record(document_type: str, suffix: str, name: str, content: str) -> PrintTemplateRecord:
    return PrintTemplateRecord(
        id=f"builtin:{document_type}:{suffix}",
        document_type=document_type,
        name=name,
        content=content.strip(),
        created_at=_BUILTIN_CREATED_AT,
        updated_at=_BUILTIN_CREATED_AT,
        source="builtin",
    )


def builtin_template_records() -> tuple[PrintTemplateRecord, ...]:
    """Return built-in templates keyed by document type for the print workspace."""
    return (
        _record(
            "repair_order",
            "standard",
            "Стандартный заказ-наряд",
            """
<div class="document-page">
  <header class="doc-head">
    <div class="doc-head__brand">
      <div class="doc-brand-mark">
        {{#service.brand_logo_data_uri}}<img src="{{service.brand_logo_data_uri}}" alt="AutoStop АВТОТЕХЦЕНТР №1">{{/service.brand_logo_data_uri}}
        {{^service.brand_logo_data_uri}}<div class="doc-brand-mark__fallback">AutoStop</div>{{/service.brand_logo_data_uri}}
      </div>
      <div class="doc-brand-copy">
        <div class="doc-kicker">Печатная форма</div>
        <h1 class="doc-title">Заказ-наряд</h1>
        <div class="doc-subtitle">№ {{repair_order.number_display}} от {{dates.document_date_display}}</div>
      </div>
    </div>
    <div class="doc-service"><div class="doc-service__name">{{service.company_name}}</div><div class="doc-service__meta">{{service.address}}</div><div class="doc-service__meta">{{service.phone}}</div></div>
  </header>
  <section class="doc-grid">
    <div class="doc-card"><div class="doc-label">Клиент</div><div class="doc-value">{{client.name_display}}</div></div>
    <div class="doc-card"><div class="doc-label">Телефон</div><div class="doc-value">{{client.phone_display}}</div></div>
    <div class="doc-card"><div class="doc-label">Автомобиль</div><div class="doc-value">{{vehicle.display_name}}</div></div>
    <div class="doc-card"><div class="doc-label">Госномер</div><div class="doc-value">{{vehicle.license_plate_display}}</div></div>
    <div class="doc-card"><div class="doc-label">VIN</div><div class="doc-value">{{vehicle.vin_display}}</div></div>
    <div class="doc-card"><div class="doc-label">Пробег</div><div class="doc-value">{{vehicle.mileage_display}}</div></div>
    <div class="doc-card"><div class="doc-label">Форма оплаты</div><div class="doc-value">{{repair_order.payment_method_label}}</div></div>
    <div class="doc-card"><div class="doc-label">Предоплата</div><div class="doc-value">{{repair_order.prepayment_display}}</div></div>
  </section>
  <section class="doc-section"><h2 class="doc-section__title">Причина обращения</h2><div class="doc-note">{{{repair_order.reason_html}}}</div></section>
  <section class="doc-section"><h2 class="doc-section__title">Информация для клиента</h2><div class="doc-note">{{{repair_order.client_information_html}}}</div></section>
  <section class="doc-section">
    <h2 class="doc-section__title">Работы</h2>
    <table class="doc-table"><thead><tr><th>Наименование</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#works}}<tr><td>{{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/works}}
      {{^works}}<tr><td class="doc-table__empty" colspan="4">Работы не указаны</td></tr>{{/works}}
    </tbody><tfoot><tr><td colspan="3">Итого работы</td><td class="doc-table__sum">{{totals.works_display}}</td></tr></tfoot></table>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Материалы / запчасти</h2>
    <table class="doc-table"><thead><tr><th>Наименование</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#materials}}<tr><td>{{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/materials}}
      {{^materials}}<tr><td class="doc-table__empty" colspan="4">Материалы не указаны</td></tr>{{/materials}}
    </tbody><tfoot><tr><td colspan="3">Итого материалы</td><td class="doc-table__sum">{{totals.materials_display}}</td></tr></tfoot></table>
  </section>
  <section class="doc-totals">
    <div class="doc-totals__row"><span>Итого работы</span><span>{{totals.works_display}}</span></div>
    <div class="doc-totals__row"><span>Итого материалы</span><span>{{totals.materials_display}}</span></div>
    <div class="doc-totals__row"><span>Стоимость заказ-наряда</span><span>{{totals.subtotal_display}}</span></div>
    {{#totals.has_taxes}}<div class="doc-totals__row"><span>Налоги и сборы</span><span>{{totals.taxes_display}}</span></div>{{/totals.has_taxes}}
    <div class="doc-totals__row"><span>Итого по заказ-наряду</span><span>{{totals.grand_display}}</span></div>
    {{#totals.has_prepayment}}<div class="doc-totals__row"><span>Предоплата</span><span>{{totals.prepayment_display}}</span></div>{{/totals.has_prepayment}}
    <div class="doc-totals__row doc-totals__row--grand"><span>К доплате</span><span>{{totals.due_display}}</span></div>
  </section>
</div>
            """,
        ),
        _record(
            "invoice",
            "standard",
            "Стандартный счет",
            """
<div class="document-page">
  <header class="doc-head">
    <div><h1 class="doc-title">Счет на оплату</h1><div class="doc-subtitle">№ {{repair_order.number_display}} от {{dates.document_date_display}}</div></div>
    <div class="doc-service"><div class="doc-service__name">{{service.company_name}}</div><div class="doc-service__meta">{{service.legal_name}}</div><div class="doc-service__meta">ИНН {{service.inn}} КПП {{service.kpp}}</div><div class="doc-service__meta">{{service.address}}</div></div>
  </header>
  <section class="doc-grid">
    <div class="doc-card doc-card--wide"><div class="doc-label">Плательщик</div><div class="doc-value">{{client.name_display}} · {{client.phone_display}}</div></div>
    <div class="doc-card doc-card--wide"><div class="doc-label">Автомобиль</div><div class="doc-value">{{vehicle.display_name}} · {{vehicle.license_plate_display}} · VIN {{vehicle.vin_display}}</div></div>
    <div class="doc-card"><div class="doc-label">Форма оплаты</div><div class="doc-value">{{repair_order.payment_method_label}}</div></div>
    <div class="doc-card"><div class="doc-label">Предоплата</div><div class="doc-value">{{repair_order.prepayment_display}}</div></div>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Позиции счета</h2>
    <table class="doc-table"><thead><tr><th>Наименование</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#line_items}}<tr><td>{{section_label}}: {{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/line_items}}
      {{^line_items}}<tr><td class="doc-table__empty" colspan="4">Нет строк для счета</td></tr>{{/line_items}}
    </tbody><tfoot><tr><td colspan="3">Стоимость заказ-наряда</td><td class="doc-table__sum">{{totals.subtotal_display}}</td></tr></tfoot></table>
  </section>
  <section class="doc-grid">
    <div class="doc-card doc-card--wide"><div class="doc-label">Основание</div><div class="doc-value">{{document.description}}</div></div>
    <div class="doc-card doc-card--wide"><div class="doc-label">Банк</div><div class="doc-value">{{service.bank_name}} · БИК {{service.bik}} · Р/с {{service.settlement_account}} · К/с {{service.correspondent_account}}</div></div>
  </section>
  <section class="doc-totals">
    <div class="doc-totals__row"><span>Без НДС / Налоговый режим</span><span>{{service.tax_label}}</span></div>
    <div class="doc-totals__row"><span>Стоимость заказ-наряда</span><span>{{totals.subtotal_display}}</span></div>
    {{#totals.has_taxes}}<div class="doc-totals__row"><span>Налоги и сборы</span><span>{{totals.taxes_display}}</span></div>{{/totals.has_taxes}}
    <div class="doc-totals__row"><span>Итого по заказ-наряду</span><span>{{totals.grand_display}}</span></div>
    {{#totals.has_prepayment}}<div class="doc-totals__row"><span>Предоплата</span><span>{{totals.prepayment_display}}</span></div>{{/totals.has_prepayment}}
    <div class="doc-totals__row doc-totals__row--grand"><span>К доплате</span><span>{{totals.due_display}}</span></div>
  </section>
</div>
            """,
        ),
        _record(
            "invoice_factura",
            "standard",
            "Стандартный счет-фактура",
            """
<div class="document-page">
  <header class="doc-head">
    <div><h1 class="doc-title">Счет-фактура</h1><div class="doc-subtitle">Документ по заказ-наряду № {{repair_order.number_display}}</div></div>
    <div class="doc-service"><div class="doc-service__name">{{service.company_name}}</div><div class="doc-service__meta">{{service.legal_name}}</div><div class="doc-service__meta">ИНН {{service.inn}} · КПП {{service.kpp}} · ОГРН {{service.ogrn}}</div></div>
  </header>
  <section class="doc-grid">
    <div class="doc-card"><div class="doc-label">Покупатель</div><div class="doc-value">{{client.name_display}}</div></div>
    <div class="doc-card"><div class="doc-label">Контакт</div><div class="doc-value">{{client.phone_display}}</div></div>
    <div class="doc-card"><div class="doc-label">Дата</div><div class="doc-value">{{dates.document_date_display}}</div></div>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Номенклатура</h2>
    <table class="doc-table"><thead><tr><th>Наименование</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#line_items}}<tr><td>{{section_label}}: {{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/line_items}}
      {{^line_items}}<tr><td class="doc-table__empty" colspan="4">Номенклатура не заполнена</td></tr>{{/line_items}}
    </tbody><tfoot><tr><td colspan="3">Всего</td><td class="doc-table__sum">{{totals.grand_display}}</td></tr></tfoot></table>
  </section>
  <section class="doc-grid">
    <div class="doc-card doc-card--wide"><div class="doc-label">Автомобиль</div><div class="doc-value">{{vehicle.display_name}} · {{vehicle.license_plate_display}} · VIN {{vehicle.vin_display}}</div></div>
    <div class="doc-card doc-card--wide"><div class="doc-label">Налоговый режим</div><div class="doc-value">{{service.tax_label}}</div></div>
  </section>
</div>
            """,
        ),
        _record(
            "inspection_sheet",
            "standard",
            "Стандартная дефектовочная ведомость",
            """
<div class="document-page">
  <header class="doc-head">
    <div><h1 class="doc-title">Дефектовочная ведомость</h1><div class="doc-subtitle">По заказ-наряду № {{repair_order.number_display}}</div></div>
    <div class="doc-service"><div class="doc-service__name">{{service.company_name}}</div><div class="doc-service__meta">{{service.address}}</div><div class="doc-service__meta">{{service.phone}}</div></div>
  </header>
  <section class="doc-grid">
    <div class="doc-card"><div class="doc-label">Клиент</div><div class="doc-value">{{inspection_sheet.client_display}}</div></div>
    <div class="doc-card"><div class="doc-label">Автомобиль</div><div class="doc-value">{{inspection_sheet.vehicle_display}}</div></div>
    <div class="doc-card"><div class="doc-label">VIN / госномер</div><div class="doc-value">{{inspection_sheet.vin_or_plate_display}}</div></div>
  </section>
  <section class="doc-section"><h2 class="doc-section__title">С чем приехал клиент</h2><div class="doc-note">{{{inspection_sheet.complaint_summary_html}}}</div></section>
  <section class="doc-section"><h2 class="doc-section__title">Что выявлено</h2><ul class="doc-list">{{#inspection_sheet.findings}}<li>{{text}}</li>{{/inspection_sheet.findings}}{{^inspection_sheet.findings}}<li>Дефекты не зафиксированы отдельным списком.</li>{{/inspection_sheet.findings}}</ul></section>
  <section class="doc-section"><h2 class="doc-section__title">Рекомендации</h2><ul class="doc-list">{{#inspection_sheet.recommendations}}<li>{{text}}</li>{{/inspection_sheet.recommendations}}{{^inspection_sheet.recommendations}}<li>Дополнительные рекомендации не указаны.</li>{{/inspection_sheet.recommendations}}</ul></section>
  <section class="doc-section">
    <h2 class="doc-section__title">Планируемые работы / материалы</h2>
    <div class="doc-grid">
      <div class="doc-card"><div class="doc-label">Работы</div><ul class="doc-list">{{#inspection_sheet.planned_works}}<li>{{text}}</li>{{/inspection_sheet.planned_works}}{{^inspection_sheet.planned_works}}<li>{{meta.works_count}} позиций</li>{{/inspection_sheet.planned_works}}</ul></div>
      <div class="doc-card"><div class="doc-label">Материалы</div><ul class="doc-list">{{#inspection_sheet.planned_materials}}<li>{{text}}</li>{{/inspection_sheet.planned_materials}}{{^inspection_sheet.planned_materials}}<li>{{meta.materials_count}} позиций</li>{{/inspection_sheet.planned_materials}}</ul></div>
      <div class="doc-card"><div class="doc-label">Комментарий мастера</div><div class="doc-value">{{inspection_sheet.master_comment_display}}</div></div>
    </div>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Перечень необходимых работ</h2>
    <table class="doc-table"><thead><tr><th class="doc-table__narrow">№</th><th>Наименование</th><th class="doc-table__narrow">Кол-во</th></tr></thead><tbody>
      {{#inspection_sheet.planned_work_rows}}<tr><td class="doc-table__narrow">{{index}}</td><td>{{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td></tr>{{/inspection_sheet.planned_work_rows}}
      {{^inspection_sheet.planned_work_rows}}<tr><td class="doc-table__empty" colspan="3">Работы не указаны</td></tr>{{/inspection_sheet.planned_work_rows}}
    </tbody></table>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Перечень необходимых запчастей / материалов</h2>
    <table class="doc-table"><thead><tr><th class="doc-table__narrow">№</th><th>Наименование</th><th class="doc-table__narrow">Кол-во</th></tr></thead><tbody>
      {{#inspection_sheet.planned_material_rows}}<tr><td class="doc-table__narrow">{{index}}</td><td>{{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td></tr>{{/inspection_sheet.planned_material_rows}}
      {{^inspection_sheet.planned_material_rows}}<tr><td class="doc-table__empty" colspan="3">Запчасти и материалы не указаны</td></tr>{{/inspection_sheet.planned_material_rows}}
    </tbody></table>
  </section>
</div>
            """,
        ),
        _record(
            "completion_act",
            "standard",
            "Стандартный акт выполненных работ",
            """
<div class="document-page">
  <header class="doc-head">
    <div><h1 class="doc-title">Акт выполненных работ</h1><div class="doc-subtitle">К заказ-наряду № {{repair_order.number_display}}</div></div>
    <div class="doc-service"><div class="doc-service__name">{{service.company_name}}</div><div class="doc-service__meta">{{service.legal_name}}</div><div class="doc-service__meta">{{service.address}}</div></div>
  </header>
  <section class="doc-grid">
    <div class="doc-card"><div class="doc-label">Клиент</div><div class="doc-value">{{client.name_display}}</div></div>
    <div class="doc-card"><div class="doc-label">Телефон</div><div class="doc-value">{{client.phone_display}}</div></div>
    <div class="doc-card"><div class="doc-label">Автомобиль</div><div class="doc-value">{{vehicle.display_name}}</div></div>
    <div class="doc-card"><div class="doc-label">Форма оплаты</div><div class="doc-value">{{repair_order.payment_method_label}}</div></div>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Выполненные работы</h2>
    <table class="doc-table"><thead><tr><th>Наименование</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#works}}<tr><td>{{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/works}}
      {{^works}}<tr><td class="doc-table__empty" colspan="4">Работы не указаны</td></tr>{{/works}}
    </tbody><tfoot><tr><td colspan="3">Итого работы</td><td class="doc-table__sum">{{totals.works_display}}</td></tr></tfoot></table>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Материалы / запчасти</h2>
    <table class="doc-table"><thead><tr><th>Наименование</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#materials}}<tr><td>{{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/materials}}
      {{^materials}}<tr><td class="doc-table__empty" colspan="4">Материалы не указаны</td></tr>{{/materials}}
    </tbody><tfoot><tr><td colspan="3">Итого материалы</td><td class="doc-table__sum">{{totals.materials_display}}</td></tr></tfoot></table>
  </section>
  <section class="doc-section"><h2 class="doc-section__title">Справка для клиента</h2><div class="doc-note">{{{repair_order.client_information_html}}}</div></section>
  <section class="doc-totals">
    <div class="doc-totals__row"><span>Итого работы</span><span>{{totals.works_display}}</span></div>
    <div class="doc-totals__row"><span>Итого материалы</span><span>{{totals.materials_display}}</span></div>
    <div class="doc-totals__row"><span>Стоимость заказ-наряда</span><span>{{totals.subtotal_display}}</span></div>
    {{#totals.has_taxes}}<div class="doc-totals__row"><span>Налоги и сборы</span><span>{{totals.taxes_display}}</span></div>{{/totals.has_taxes}}
    <div class="doc-totals__row"><span>Итого по заказ-наряду</span><span>{{totals.grand_display}}</span></div>
    {{#totals.has_prepayment}}<div class="doc-totals__row"><span>Предоплата</span><span>{{totals.prepayment_display}}</span></div>{{/totals.has_prepayment}}
    <div class="doc-totals__row doc-totals__row--grand"><span>К доплате</span><span>{{totals.due_display}}</span></div>
  </section>
  <section class="doc-signatures">
    <div class="doc-signatures__item"><div>Исполнитель</div><div class="doc-hint">{{service.company_name}}</div></div>
    <div class="doc-signatures__item"><div>Заказчик</div><div class="doc-hint">{{client.name_display}}</div></div>
  </section>
</div>
            """,
        ),
    )
