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
        id="vehicle_acceptance_act",
        label="Акт приема автомобиля",
        description="Прием автомобиля в работу, фотофиксация и важные условия.",
        default_template_id="builtin:vehicle_acceptance_act:standard",
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
    PrintDocumentDefinition(
        id="parts_sale",
        label="Продажа запчастей",
        description="Документ продажи запчастей и материалов без привязки к автомобилю.",
        default_template_id="builtin:parts_sale:standard",
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
    width: 82px;
    height: 82px;
    border-radius: 18px;
    border: 1px solid rgba(0, 0, 0, 0.08);
    background: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 8px;
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
  .doc-kicker { color: var(--paper-soft); font-size: 9px; text-transform: uppercase; letter-spacing: 0.12em; }
  .doc-title { margin: 0; font-size: 22px; line-height: 1.05; font-weight: 700; letter-spacing: 0.01em; }
  .doc-subtitle { color: var(--paper-soft); font-size: 11px; }
  .doc-service { text-align: right; max-width: 330px; }
  .doc-service__name { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
  .doc-service__meta { color: var(--paper-soft); white-space: pre-wrap; }
  .doc-head-table { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
  .doc-head-table__left { width: 58%; vertical-align: top; padding-right: 12px; }
  .doc-head-table__right { width: 42%; vertical-align: top; text-align: right; }
  .doc-banner-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 12px;
    border: 1px solid var(--paper-line-strong);
    border-radius: 10px;
  }
  .doc-banner-table td { padding: 10px 12px; vertical-align: middle; }
  .doc-banner-table__phone { color: var(--paper-accent); font-size: 21px; font-weight: 700; line-height: 1.05; }
  .doc-banner-table__copy { color: var(--paper-soft); font-size: 11px; line-height: 1.45; text-align: right; }
  .doc-invoice-notice {
    margin-bottom: 12px;
    border: 1px solid var(--paper-line-strong);
    border-radius: 8px;
    padding: 8px 10px;
    color: var(--paper-soft);
    font-size: 10px;
    line-height: 1.4;
    background: rgba(0, 0, 0, 0.02);
  }
  .doc-meta-table { width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 12px; }
  .doc-meta-table td {
    border: 1px solid var(--paper-line);
    border-radius: 8px;
    padding: 8px 10px;
    vertical-align: top;
    background: rgba(255,255,255,0.65);
    width: 33.333%;
  }
  .doc-bank-table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
    margin-bottom: 14px;
    font-size: 10.5px;
  }
  .doc-bank-table td {
    border: 1px solid var(--paper-line-strong);
    padding: 6px 8px;
    vertical-align: top;
  }
  .doc-bank-table__label { color: var(--paper-soft); font-size: 9px; text-transform: uppercase; letter-spacing: 0.06em; }
  .doc-bank-table__value { line-height: 1.25; margin-top: 2px; word-break: break-word; }
  .doc-bank-table__value strong { font-size: 11px; }
  .doc-checkbox-row { display: flex; gap: 18px; align-items: center; font-weight: 700; }
  .doc-checkbox { display: inline-block; width: 12px; height: 12px; border: 1px solid var(--paper-line-strong); margin-right: 6px; vertical-align: -2px; }
  .doc-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
  .doc-card { border: 1px solid var(--paper-line); border-radius: 8px; padding: 8px 10px; min-height: 54px; background: rgba(255,255,255,0.65); }
  .doc-card--wide { grid-column: span 3; }
  .doc-banner {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: center;
    margin-bottom: 12px;
    padding: 10px 12px;
    border: 1px solid var(--paper-line-strong);
    border-radius: 10px;
    background: linear-gradient(180deg, rgba(0, 0, 0, 0.02), rgba(255, 255, 255, 0.96));
    break-inside: avoid;
  }
  .doc-banner__label { color: var(--paper-soft); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 2px; }
  .doc-banner__phone { color: var(--paper-accent); font-size: 21px; font-weight: 700; line-height: 1.05; }
  .doc-banner__copy { color: var(--paper-soft); font-size: 11px; line-height: 1.45; text-align: right; max-width: 260px; }
  .doc-label { color: var(--paper-soft); font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 5px; }
  .doc-value { white-space: pre-wrap; word-break: break-word; }
  .doc-section { margin-bottom: 12px; break-inside: avoid; }
  .doc-section--warranty { break-inside: auto; }
  .doc-section__title { margin: 0 0 6px; font-size: 13px; font-weight: 700; }
  .doc-note { border: 1px solid var(--paper-line); border-radius: 9px; padding: 9px 11px; min-height: 54px; white-space: normal; line-height: 1.5; background: #fcfcfc; }
  .doc-terms {
    border: 1px solid rgba(0, 0, 0, 0.09);
    border-radius: 10px;
    padding: 9px 11px;
    background: linear-gradient(180deg, rgba(0, 0, 0, 0.015), rgba(255, 255, 255, 0.92));
    font-size: 10px;
    line-height: 1.38;
  }
  .doc-terms--compact {
    font-size: 9.5px;
    line-height: 1.34;
  }
  .doc-terms__lead {
    margin: 0 0 6px;
    font-size: 10px;
    color: var(--paper-soft);
  }
  .doc-terms__list {
    margin: 0;
    padding-left: 16px;
    display: grid;
    gap: 4px;
  }
  .doc-terms__list--compact {
    gap: 3px;
    padding-left: 15px;
  }
  .doc-terms__list li { break-inside: avoid; }
  .doc-terms__list p { margin: 0; }
  .doc-terms__list strong { color: var(--paper-accent); }
  .doc-table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 11px; }
  .doc-table th, .doc-table td { border: 1px solid var(--paper-line); padding: 6px 7px; vertical-align: top; text-align: left; }
  .doc-table th { font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--paper-accent); background: rgba(0, 0, 0, 0.025); }
  .doc-table tbody tr:nth-child(even) td { background: rgba(0, 0, 0, 0.012); }
  .doc-table__narrow { width: 12%; text-align: right !important; font-variant-numeric: tabular-nums; }
  .doc-table__sum { width: 16%; text-align: right !important; font-variant-numeric: tabular-nums; }
  .doc-table__empty { color: var(--paper-soft); text-align: center; }
  .doc-table tfoot td { font-weight: 700; }
  .doc-totals { margin-top: 10px; margin-left: auto; width: min(400px, 100%); border: 1px solid var(--paper-line-strong); border-radius: 10px; overflow: hidden; }
  .doc-totals__row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: baseline; padding: 8px 11px; border-bottom: 1px solid var(--paper-line); font-variant-numeric: tabular-nums; }
  .doc-totals__row:last-child { border-bottom: 0; }
  .doc-totals__row > span:last-child { text-align: right; }
  .doc-totals__row--grand { font-size: 14px; font-weight: 700; background: rgba(0, 0, 0, 0.03); }
  .doc-totals-table { width: min(400px, 100%); margin-left: auto; margin-top: 10px; border-collapse: collapse; }
  .doc-totals-table td {
    border: 1px solid var(--paper-line);
    padding: 8px 11px;
    font-variant-numeric: tabular-nums;
    background: rgba(255,255,255,0.65);
  }
  .doc-totals-table td:last-child { text-align: right; width: 32%; }
  .doc-totals-table__grand td { font-size: 14px; font-weight: 700; background: rgba(0, 0, 0, 0.03); }
  .doc-invoice-words {
    margin-top: 10px;
    border: 1px solid var(--paper-line);
    border-radius: 8px;
    padding: 8px 10px;
    background: #fcfcfc;
    font-size: 11px;
    line-height: 1.4;
  }
  .doc-invoice-words strong { color: var(--paper-accent); }
  .doc-list { margin: 0; padding-left: 18px; }
  .doc-list li + li { margin-top: 4px; }
  .doc-signatures { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px; margin-top: 4px; }
  .doc-signatures__item { padding-top: 3px; }
  .doc-signatures__role { font-size: 12px; font-weight: 700; color: var(--paper-accent); }
  .doc-signatures__line { border-bottom: 1px solid var(--paper-line-strong); min-height: 28px; margin-top: 10px; }
  .doc-signatures__note { color: var(--paper-soft); font-size: 10px; line-height: 1.35; margin-top: 6px; }
  .doc-signatures-table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  .doc-signatures-table td { width: 50%; vertical-align: top; padding-right: 12px; }
  .doc-signatures-table td + td { padding-left: 12px; padding-right: 0; }
  .doc-signature-line { border-bottom: 1px solid var(--paper-line-strong); height: 30px; }
  .doc-signature-caption { color: var(--paper-soft); font-size: 10px; margin-top: 5px; }
  .doc-hint { color: var(--paper-soft); font-size: 11px; }
  .doc-page-break { display: block; height: 0; clear: both; page-break-before: always; page-break-after: always; }
  @page { size: A4; margin: 9mm; }
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


_REPAIR_ORDER_WARRANTY_TERMS_HTML = """
<p class="doc-terms__lead">Ниже приведены условия гарантии, которые действуют для выполненных работ и установленных деталей.</p>
<ol class="doc-terms__list">
  <li><strong>30 дней:</strong> гарантия на выполненные работы и замененные запасные части. На запасные части, предоставленные заказчиком, гарантия не распространяется.</li>
  <li><strong>Запчасти клиента:</strong> гарантия на них не распространяется.</li>
  <li><strong>Неоригинальные и Б/У детали:</strong> гарантия не распространяется.</li>
  <li><strong>Возврат замененных деталей:</strong> детали передаются заказчику по его требованию, заявленному до начала ремонта, в день выдачи автомобиля. Если требование не заявлено, детали утилизируются в процессе ремонта.</li>
  <li><strong>Автоэлектрика и электропроводка:</strong> гарантия составляет 10 (десять) календарных дней.</li>
  <li><strong>Диагностика и восстановительные работы:</strong> гарантия на результаты диагностики и на процедуры восстановления узлов и деталей, выполненные не заводским методом, не распространяется. Гарантия действует только на качество выполненных работ и на реализованные оригинальные запасные части.</li>
  <li><strong>АКПП и ДВС:</strong> гарантия на механическую часть составляет 180 (сто восемьдесят) календарных дней, если иное не оговорено отдельно.</li>
  <li><strong>ДВС:</strong> гарантия распространяется только на узлы, которые ремонтировались или заменялись.</li>
  <li><strong>АКПП:</strong> гарантия не распространяется на гидротрансформатор, гидроблок, блок управления и соленоиды.</li>
  <li><strong>Исключения:</strong> при ремонте АКПП восстанавливается только механическая часть коробки передач. Если после ремонта выявляется неисправность блока управления КПП, расходы на его ремонт или замену несет заказчик. Гарантия не распространяется на ремонт топливных форсунок, турбокомпрессоров, гидроблоков и соленоидов. Гарантия действует только на замененные оригинальные детали и работы по их установке.</li>
</ol>
""".strip()


def builtin_template_records() -> tuple[PrintTemplateRecord, ...]:
    """Return built-in templates keyed by document type for the print workspace."""
    return (
        _record(
            "repair_order",
            "standard",
            "Стандартный заказ-наряд",
            """
<div class="document-page">
  <table class="doc-head-table">
    <tr>
      <td class="doc-head-table__left">
        <table class="doc-head-table" style="margin-bottom:0;">
          <tr>
            <td style="width:104px; vertical-align:top; padding-right:12px;">
              <div class="doc-brand-mark">
                {{#service.brand_logo_data_uri}}<img src="{{service.brand_logo_data_uri}}" width="70" height="70" style="width:70px;height:70px;" alt="AutoStop АВТОТЕХЦЕНТР №1">{{/service.brand_logo_data_uri}}
                {{^service.brand_logo_data_uri}}<div class="doc-brand-mark__fallback">AutoStop</div>{{/service.brand_logo_data_uri}}
              </div>
            </td>
            <td style="vertical-align:top;">
              <div class="doc-brand-copy">
                <div class="doc-kicker">Печатная форма</div>
                <h1 class="doc-title">Заказ-наряд</h1>
                <div class="doc-subtitle">№ {{repair_order.number_display}} от {{dates.document_date_display}}</div>
              </div>
            </td>
          </tr>
        </table>
      </td>
      <td class="doc-head-table__right">
        <div class="doc-service"><div class="doc-service__name">{{service.company_name}}</div><div class="doc-service__meta">{{service.address}}</div><div class="doc-service__meta">{{service.phone}}</div></div>
      </td>
    </tr>
  </table>
  <table class="doc-banner-table">
    <tr>
      <td>
        <div class="doc-banner__label">Телефон ресепшена</div>
        <div class="doc-banner-table__phone">{{#service.reception_phone}}{{service.reception_phone}}{{/service.reception_phone}}{{^service.reception_phone}}{{service.phone}}{{/service.reception_phone}}</div>
      </td>
      <td class="doc-banner-table__copy">Прием автомобиля, запись и вопросы по заказ-наряду</td>
    </tr>
  </table>
  <table class="doc-meta-table">
    <tr>
      <td><div class="doc-label">Клиент</div><div class="doc-value">{{client.name_display}}</div></td>
      <td><div class="doc-label">Телефон</div><div class="doc-value">{{client.phone_display}}</div></td>
      <td><div class="doc-label">Автомобиль</div><div class="doc-value">{{vehicle.display_name}}</div></td>
    </tr>
    <tr>
      <td><div class="doc-label">Госномер</div><div class="doc-value">{{vehicle.license_plate_display}}</div></td>
      <td><div class="doc-label">VIN</div><div class="doc-value">{{vehicle.vin_display}}</div></td>
      <td><div class="doc-label">Пробег</div><div class="doc-value">{{vehicle.mileage_display}}</div></td>
    </tr>
  </table>
  <section class="doc-section"><h2 class="doc-section__title">Причина обращения</h2><div class="doc-note">{{{repair_order.reason_html}}}</div></section>
  <section class="doc-section"><h2 class="doc-section__title">Информация для клиента</h2><div class="doc-note">{{{repair_order.client_information_html}}}</div></section>
  <section class="doc-section">
    <h2 class="doc-section__title">Работы</h2>
    <table class="doc-table"><colgroup><col><col style="width: 12%"><col style="width: 16%"><col style="width: 16%"></colgroup><thead><tr><th>Наименование</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#works}}<tr><td>{{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/works}}
      {{^works}}<tr><td class="doc-table__empty" colspan="4">Работы не указаны</td></tr>{{/works}}
    </tbody><tfoot><tr><td colspan="3">Итого работы</td><td class="doc-table__sum">{{totals.works_display}}</td></tr></tfoot></table>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Материалы / запчасти</h2>
    <table class="doc-table"><colgroup><col><col style="width: 12%"><col style="width: 16%"><col style="width: 16%"></colgroup><thead><tr><th>Наименование</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#materials}}<tr><td>{{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/materials}}
      {{^materials}}<tr><td class="doc-table__empty" colspan="4">Материалы не указаны</td></tr>{{/materials}}
    </tbody><tfoot><tr><td colspan="3">Итого материалы</td><td class="doc-table__sum">{{totals.materials_display}}</td></tr></tfoot></table>
  </section>
  <table class="doc-totals-table">
    <tr><td>Итого работы</td><td>{{totals.works_display}}</td></tr>
    <tr><td>Итого материалы</td><td>{{totals.materials_display}}</td></tr>
    <tr><td>Стоимость заказ-наряда</td><td>{{totals.subtotal_display}}</td></tr>
    {{#totals.has_taxes}}<tr><td>Налоги и сборы</td><td>{{totals.taxes_display}}</td></tr>{{/totals.has_taxes}}
    <tr><td>Итого по заказ-наряду</td><td>{{totals.grand_display}}</td></tr>
    {{#totals.has_prepayment}}<tr><td>Предоплата</td><td>{{totals.prepayment_display}}</td></tr>{{/totals.has_prepayment}}
    <tr class="doc-totals-table__grand"><td>К доплате</td><td>{{totals.due_display}}</td></tr>
  </table>
  <section class="doc-section doc-section--warranty doc-section--warranty-summary">
    <h2 class="doc-section__title">Ключевые условия</h2>
    <div class="doc-terms doc-terms--compact">{{{repair_order.terms_summary_html}}}</div>
  </section>
</div>
<!-- AUTOSTOPCRM_PAGE_BREAK -->
<div class="document-page">
  <section class="doc-section doc-section--warranty">
    <h2 class="doc-section__title">Гарантийные и важные условия</h2>
    <div class="doc-terms">{{{repair_order.warranty_terms_html}}}</div>
  </section>
  <section class="doc-section doc-section--signatures">
    <h2 class="doc-section__title">Подписи сторон</h2>
    <table class="doc-signatures-table">
      <tr>
        <td>
          <div class="doc-signatures__role">Администратор</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signatures__note">Подпись / расшифровка</div>
        </td>
        <td>
          <div class="doc-signatures__role">Клиент</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signatures__note">Автомобиль получил, претензий не имею. Согласие на обработку персональных данных подтверждаю.</div>
        </td>
      </tr>
    </table>
  </section>
</div>
            """,
        ),
        _record(
            "vehicle_acceptance_act",
            "standard",
            "Акт приема автомобиля в работу",
            """
<div class="document-page">
  <table class="doc-head-table">
    <tr>
      <td class="doc-head-table__left">
        <table class="doc-head-table" style="margin-bottom:0;">
          <tr>
            <td style="width:96px; vertical-align:top; padding-right:12px;">
              <div class="doc-brand-mark">
                {{#service.brand_logo_data_uri}}<img src="{{service.brand_logo_data_uri}}" width="70" height="70" style="width:70px;height:70px;" alt="AutoStop">{{/service.brand_logo_data_uri}}
                {{^service.brand_logo_data_uri}}<div class="doc-brand-mark__fallback">AutoStop</div>{{/service.brand_logo_data_uri}}
              </div>
            </td>
            <td style="vertical-align:top;">
              <div class="doc-kicker">Прием автомобиля</div>
              <h1 class="doc-title">Акт приема-передачи автомобиля в работу</h1>
              <div class="doc-subtitle">№ {{repair_order.number_display}} от {{dates.document_date_display}}</div>
            </td>
          </tr>
        </table>
      </td>
      <td class="doc-head-table__right">
        <div class="doc-service">
          <div class="doc-service__name">{{service.legal_name}}</div>
          <div class="doc-service__meta">{{service.address}}</div>
          <div class="doc-service__meta">Ресепшн: {{service.reception_phone}}</div>
        </div>
      </td>
    </tr>
  </table>
  <table class="doc-meta-table">
    <tr>
      <td><div class="doc-label">Заказчик</div><div class="doc-value">{{client.name_display}}</div></td>
      <td><div class="doc-label">Телефон</div><div class="doc-value">{{client.phone_display}}</div></td>
      <td><div class="doc-label">Ориентировочная стоимость</div><div class="doc-value">{{vehicle_acceptance_act.estimated_cost_display}}</div></td>
    </tr>
    <tr>
      <td><div class="doc-label">Марка / модель</div><div class="doc-value">{{vehicle.display_name}}</div></td>
      <td><div class="doc-label">VIN</div><div class="doc-value">{{vehicle.vin_display}}</div></td>
      <td><div class="doc-label">Госномер / пробег</div><div class="doc-value">{{vehicle.license_plate_display}} / {{vehicle.mileage_display}}</div></td>
    </tr>
  </table>
  <section class="doc-section">
    <h2 class="doc-section__title">Какой ремонт необходимо выполнить?</h2>
    <div class="doc-note">{{{repair_order.reason_html}}}</div>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Фотофиксация состояния автомобиля</h2>
    <div class="doc-note">
      <div class="doc-checkbox-row"><span><span class="doc-checkbox"></span>ДА</span><span><span class="doc-checkbox"></span>НЕТ</span></div>
      <div class="doc-hint">Претензии по повреждениям кузова принимаются только если фотофиксация проведена при сдаче автомобиля совместно с представителем сервиса.</div>
    </div>
  </section>
  <section class="doc-section doc-section--warranty">
    <h2 class="doc-section__title">Ознакомьтесь, это важно</h2>
    <div class="doc-terms">{{{repair_order.acceptance_terms_html}}}</div>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Подтверждение</h2>
    <table class="doc-signatures-table">
      <tr>
        <td>
          <div class="doc-signatures__role">Клиент</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signature-caption">С условиями приема автомобиля согласен</div>
        </td>
        <td>
          <div class="doc-signatures__role">Администратор</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signature-caption">Автомобиль принял</div>
        </td>
      </tr>
    </table>
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
  <table class="doc-head-table">
    <tr>
      <td class="doc-head-table__left">
        <table class="doc-head-table" style="margin-bottom:0;">
          <tr>
            <td style="width:104px; vertical-align:top; padding-right:12px;">
              <div class="doc-brand-mark">
                {{#service.brand_logo_data_uri}}<img src="{{service.brand_logo_data_uri}}" width="70" height="70" style="width:70px;height:70px;" alt="AutoStop">{{/service.brand_logo_data_uri}}
                {{^service.brand_logo_data_uri}}<div class="doc-brand-mark__fallback">AutoStop</div>{{/service.brand_logo_data_uri}}
              </div>
            </td>
            <td style="vertical-align:top;">
              <div class="doc-brand-copy">
                <div class="doc-kicker">Платежный документ</div>
                <h1 class="doc-title">Счет на оплату</h1>
                <div class="doc-subtitle">№ {{repair_order.number_display}} от {{dates.document_date_display}}</div>
              </div>
            </td>
          </tr>
        </table>
      </td>
      <td class="doc-head-table__right">
        <div class="doc-service">
          <div class="doc-service__name">{{service.company_name}}</div>
          <div class="doc-service__meta">{{service.legal_name}}</div>
          <div class="doc-service__meta">{{service.address}}</div>
          <div class="doc-service__meta">Тел. {{service.reception_phone}} · {{service.website}}</div>
        </div>
      </td>
    </tr>
  </table>
  <div class="doc-invoice-notice">
    Внимание! Оплата данного счета означает согласие с указанными в нем работами, материалами и условиями поставки. Уведомление об оплате обязательно.
  </div>
  <table class="doc-bank-table">
    <colgroup>
      <col style="width: 34%">
      <col style="width: 24%">
      <col style="width: 20%">
      <col style="width: 22%">
    </colgroup>
    <tr>
      <td colspan="2"><div class="doc-bank-table__label">Банк получателя</div><div class="doc-bank-table__value"><strong>{{service.bank_name}}</strong></div></td>
      <td><div class="doc-bank-table__label">БИК</div><div class="doc-bank-table__value"><strong>{{service.bik}}</strong></div></td>
      <td><div class="doc-bank-table__label">Сч. №</div><div class="doc-bank-table__value"><strong>{{service.correspondent_account}}</strong></div></td>
    </tr>
    <tr>
      <td><div class="doc-bank-table__label">ИНН</div><div class="doc-bank-table__value"><strong>{{service.inn}}</strong></div></td>
      <td><div class="doc-bank-table__label">КПП</div><div class="doc-bank-table__value"><strong>{{service.kpp}}</strong></div></td>
      <td colspan="2"><div class="doc-bank-table__label">Сч. №</div><div class="doc-bank-table__value"><strong>{{service.settlement_account}}</strong></div></td>
    </tr>
    <tr>
      <td colspan="4"><div class="doc-bank-table__label">Получатель</div><div class="doc-bank-table__value"><strong>{{service.legal_name}}</strong></div></td>
    </tr>
  </table>
  <section class="doc-section">
    <h2 class="doc-section__title">Сведения по счету</h2>
    <table class="doc-meta-table">
      <tr>
        <td><div class="doc-label">Поставщик</div><div class="doc-value">{{service.legal_name}}, ИНН {{service.inn}}, {{service.address}}, тел. {{service.reception_phone}}</div></td>
        <td><div class="doc-label">Покупатель</div><div class="doc-value">{{client.name_display}} · {{client.phone_display}}</div></td>
        <td><div class="doc-label">Автомобиль</div><div class="doc-value">{{vehicle.display_name}} · {{vehicle.license_plate_display}} · VIN {{vehicle.vin_display}}</div></td>
      </tr>
    </table>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Позиции счета</h2>
    <table class="doc-table"><colgroup><col style="width: 7%"><col><col style="width: 12%"><col style="width: 12%"><col style="width: 15%"><col style="width: 16%"></colgroup><thead><tr><th class="doc-table__narrow">№</th><th>Наименование товара, работ, услуг</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__narrow">Ед. изм.</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#line_items}}<tr><td class="doc-table__narrow">{{index}}</td><td>{{section_label}}: {{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__narrow">{{unit_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/line_items}}
      {{^line_items}}<tr><td class="doc-table__empty" colspan="6">Нет строк для счета</td></tr>{{/line_items}}
    </tbody><tfoot><tr><td colspan="5">Итого</td><td class="doc-table__sum">{{totals.subtotal_display}}</td></tr></tfoot></table>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Назначение платежа</h2>
    <div class="doc-note">{{service.payment_purpose}}</div>
  </section>
  <table class="doc-totals-table">
    <tr><td>Итого</td><td>{{totals.subtotal_display}}</td></tr>
    {{#totals.has_taxes}}<tr><td>В том числе налоги и сборы</td><td>{{totals.taxes_display}}</td></tr>{{/totals.has_taxes}}
    {{#totals.has_prepayment}}<tr><td>Предоплата</td><td>{{totals.prepayment_display}}</td></tr>{{/totals.has_prepayment}}
    <tr class="doc-totals-table__grand"><td>Всего к оплате</td><td>{{totals.due_display}}</td></tr>
  </table>
  <div class="doc-invoice-words">Сумма прописью: <strong>{{totals.due_words_display}}</strong></div>
  <section class="doc-section">
    <h2 class="doc-section__title">Подписи</h2>
    <table class="doc-signatures-table">
      <tr>
        <td>
          <div class="doc-signatures__role">Руководитель</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signature-caption">{{service.legal_name}}</div>
        </td>
        <td>
          <div class="doc-signatures__role">Бухгалтер</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signature-caption">{{service.legal_name}}</div>
        </td>
      </tr>
    </table>
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
  <table class="doc-head-table">
    <tr>
      <td class="doc-head-table__left">
        <table class="doc-head-table" style="margin-bottom:0;">
          <tr>
            <td style="width:104px; vertical-align:top; padding-right:12px;">
              <div class="doc-brand-mark">
                {{#service.brand_logo_data_uri}}<img src="{{service.brand_logo_data_uri}}" width="70" height="70" style="width:70px;height:70px;" alt="AutoStop">{{/service.brand_logo_data_uri}}
                {{^service.brand_logo_data_uri}}<div class="doc-brand-mark__fallback">AutoStop</div>{{/service.brand_logo_data_uri}}
              </div>
            </td>
            <td style="vertical-align:top;">
              <div class="doc-brand-copy">
                <div class="doc-kicker">Бухгалтерский документ</div>
                <h1 class="doc-title">Счет-фактура</h1>
                <div class="doc-subtitle">По заказ-наряду № {{repair_order.number_display}} от {{dates.document_date_display}}</div>
              </div>
            </td>
          </tr>
        </table>
      </td>
      <td class="doc-head-table__right">
        <div class="doc-service">
          <div class="doc-service__name">{{service.company_name}}</div>
          <div class="doc-service__meta">{{service.legal_name}}</div>
          <div class="doc-service__meta">Тел. {{service.reception_phone}} · {{service.website}}</div>
          <div class="doc-service__meta">ИНН {{service.inn}} · КПП {{service.kpp}} · ОГРН {{service.ogrn}}</div>
        </div>
      </td>
    </tr>
  </table>
  <section class="doc-section">
    <h2 class="doc-section__title">Сведения по счету-фактуре</h2>
    <table class="doc-meta-table">
      <tr>
        <td><div class="doc-label">Покупатель</div><div class="doc-value">{{client.name_display}}</div></td>
        <td><div class="doc-label">Контакт</div><div class="doc-value">{{client.phone_display}}</div></td>
        <td><div class="doc-label">Дата</div><div class="doc-value">{{dates.document_date_display}}</div></td>
      </tr>
    </table>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Номенклатура</h2>
    <table class="doc-table"><thead><tr><th>Наименование</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#line_items}}<tr><td>{{section_label}}: {{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/line_items}}
      {{^line_items}}<tr><td class="doc-table__empty" colspan="4">Номенклатура не заполнена</td></tr>{{/line_items}}
    </tbody><tfoot><tr><td colspan="3">Всего</td><td class="doc-table__sum">{{totals.grand_display}}</td></tr></tfoot></table>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Сведения по заказу</h2>
    <table class="doc-meta-table">
      <tr>
        <td><div class="doc-label">Автомобиль</div><div class="doc-value">{{vehicle.display_name}} · {{vehicle.license_plate_display}} · VIN {{vehicle.vin_display}}</div></td>
        <td><div class="doc-label">Налоговый режим</div><div class="doc-value">{{service.tax_label}}</div></td>
        <td><div class="doc-label">Назначение платежа</div><div class="doc-value">{{service.payment_purpose}}</div></td>
      </tr>
    </table>
  </section>
  <table class="doc-totals-table">
    <tr><td>Налоговый режим</td><td>{{service.tax_label}}</td></tr>
    <tr><td>Итого по счету-фактуре</td><td>{{totals.grand_display}}</td></tr>
    {{#totals.has_taxes}}<tr><td>Налоги и сборы</td><td>{{totals.taxes_display}}</td></tr>{{/totals.has_taxes}}
    {{#totals.has_prepayment}}<tr><td>Предоплата</td><td>{{totals.prepayment_display}}</td></tr>{{/totals.has_prepayment}}
    <tr class="doc-totals-table__grand"><td>К оплате</td><td>{{totals.due_display}}</td></tr>
  </table>
  <section class="doc-section">
    <h2 class="doc-section__title">Подписи</h2>
    <table class="doc-signatures-table">
      <tr>
        <td>
          <div class="doc-signatures__role">Руководитель</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signature-caption">{{service.legal_name}}</div>
        </td>
        <td>
          <div class="doc-signatures__role">Бухгалтер</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signature-caption">{{service.legal_name}}</div>
        </td>
      </tr>
    </table>
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
  <table class="doc-head-table">
    <tr>
      <td class="doc-head-table__left">
        <table class="doc-head-table" style="margin-bottom:0;">
          <tr>
            <td style="width:96px; vertical-align:top; padding-right:12px;">
              <div class="doc-brand-mark">
                {{#service.brand_logo_data_uri}}<img src="{{service.brand_logo_data_uri}}" width="70" height="70" style="width:70px;height:70px;" alt="AutoStop">{{/service.brand_logo_data_uri}}
                {{^service.brand_logo_data_uri}}<div class="doc-brand-mark__fallback">AutoStop</div>{{/service.brand_logo_data_uri}}
              </div>
            </td>
            <td style="vertical-align:top;">
              <div class="doc-brand-copy">
                <div class="doc-kicker">Диагностика и дефектовка</div>
                <h1 class="doc-title">Дефектовочная ведомость</h1>
                <div class="doc-subtitle">По заказ-наряду № {{repair_order.number_display}} от {{dates.document_date_display}}</div>
              </div>
            </td>
          </tr>
        </table>
      </td>
      <td class="doc-head-table__right">
        <div class="doc-service">
          <div class="doc-service__name">{{service.company_name}}</div>
          <div class="doc-service__meta">{{service.legal_name}}</div>
          <div class="doc-service__meta">{{service.address}}</div>
          <div class="doc-service__meta">Тел. {{service.reception_phone}} · {{service.website}}</div>
        </div>
      </td>
    </tr>
  </table>
  <section class="doc-section">
    <h2 class="doc-section__title">Сведения по заказу</h2>
    <table class="doc-meta-table">
      <tr>
        <td><div class="doc-label">Клиент</div><div class="doc-value">{{inspection_sheet.client_display}}</div></td>
        <td><div class="doc-label">Автомобиль</div><div class="doc-value">{{inspection_sheet.vehicle_display}}</div></td>
        <td><div class="doc-label">VIN / госномер</div><div class="doc-value">{{inspection_sheet.vin_or_plate_display}}</div></td>
      </tr>
      <tr>
        <td><div class="doc-label">Работы в плане</div><div class="doc-value">{{inspection_sheet.planned_works_count}} позиций</div></td>
        <td><div class="doc-label">Материалы в плане</div><div class="doc-value">{{inspection_sheet.planned_materials_count}} позиций</div></td>
        <td><div class="doc-label">Комментарий мастера</div><div class="doc-value">{{inspection_sheet.master_comment_display}}</div></td>
      </tr>
    </table>
  </section>
  <section class="doc-section"><h2 class="doc-section__title">С чем приехал клиент</h2><div class="doc-note">{{{inspection_sheet.complaint_summary_html}}}</div></section>
  <section class="doc-section"><h2 class="doc-section__title">Что выявлено</h2><ul class="doc-list">{{#inspection_sheet.findings}}<li>{{text}}</li>{{/inspection_sheet.findings}}{{^inspection_sheet.findings}}<li>Дефекты не зафиксированы отдельным списком.</li>{{/inspection_sheet.findings}}</ul></section>
  <section class="doc-section"><h2 class="doc-section__title">Рекомендации</h2><ul class="doc-list">{{#inspection_sheet.recommendations}}<li>{{text}}</li>{{/inspection_sheet.recommendations}}{{^inspection_sheet.recommendations}}<li>Дополнительные рекомендации не указаны.</li>{{/inspection_sheet.recommendations}}</ul></section>
  <section class="doc-section">
    <h2 class="doc-section__title">Планируемые работы / материалы</h2>
    <table class="doc-meta-table">
      <tr>
        <td><div class="doc-label">Работы</div><ul class="doc-list">{{#inspection_sheet.planned_works}}<li>{{text}}</li>{{/inspection_sheet.planned_works}}{{^inspection_sheet.planned_works}}<li>{{inspection_sheet.planned_works_count}} позиций</li>{{/inspection_sheet.planned_works}}</ul></td>
        <td><div class="doc-label">Материалы</div><ul class="doc-list">{{#inspection_sheet.planned_materials}}<li>{{text}}</li>{{/inspection_sheet.planned_materials}}{{^inspection_sheet.planned_materials}}<li>{{inspection_sheet.planned_materials_count}} позиций</li>{{/inspection_sheet.planned_materials}}</ul></td>
        <td><div class="doc-label">Комментарий мастера</div><div class="doc-value">{{inspection_sheet.master_comment_display}}</div></td>
      </tr>
    </table>
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
  <section class="doc-section">
    <h2 class="doc-section__title">Подтверждение</h2>
    <table class="doc-signatures-table">
      <tr>
        <td>
          <div class="doc-signatures__role">Мастер-приемщик</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signature-caption">Дефектовка выполнена</div>
        </td>
        <td>
          <div class="doc-signatures__role">Клиент</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signature-caption">С результатами ознакомлен</div>
        </td>
      </tr>
    </table>
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
  <table class="doc-head-table">
    <tr>
      <td class="doc-head-table__left">
        <table class="doc-head-table" style="margin-bottom:0;">
          <tr>
            <td style="width:104px; vertical-align:top; padding-right:12px;">
              <div class="doc-brand-mark">
                {{#service.brand_logo_data_uri}}<img src="{{service.brand_logo_data_uri}}" width="70" height="70" style="width:70px;height:70px;" alt="AutoStop">{{/service.brand_logo_data_uri}}
                {{^service.brand_logo_data_uri}}<div class="doc-brand-mark__fallback">AutoStop</div>{{/service.brand_logo_data_uri}}
              </div>
            </td>
            <td style="vertical-align:top;">
              <div class="doc-brand-copy">
                <div class="doc-kicker">Закрывающий документ</div>
                <h1 class="doc-title">Акт выполненных работ</h1>
                <div class="doc-subtitle">К заказ-наряду № {{repair_order.number_display}} от {{dates.document_date_display}}</div>
              </div>
            </td>
          </tr>
        </table>
      </td>
      <td class="doc-head-table__right">
        <div class="doc-service">
          <div class="doc-service__name">{{service.company_name}}</div>
          <div class="doc-service__meta">{{service.legal_name}}</div>
          <div class="doc-service__meta">{{service.address}}</div>
          <div class="doc-service__meta">Тел. {{service.reception_phone}}</div>
        </div>
      </td>
    </tr>
  </table>
  <table class="doc-banner-table">
    <tr>
      <td>
        <div class="doc-banner__label">Телефон ресепшена</div>
        <div class="doc-banner-table__phone">{{#service.reception_phone}}{{service.reception_phone}}{{/service.reception_phone}}{{^service.reception_phone}}{{service.phone}}{{/service.reception_phone}}</div>
      </td>
      <td class="doc-banner-table__copy">Выдача автомобиля, согласование ремонта и вопросы по заказ-наряду</td>
    </tr>
  </table>
  <table class="doc-meta-table">
    <tr>
      <td><div class="doc-label">Клиент</div><div class="doc-value">{{client.name_display}}</div></td>
      <td><div class="doc-label">Телефон</div><div class="doc-value">{{client.phone_display}}</div></td>
      <td><div class="doc-label">Автомобиль</div><div class="doc-value">{{vehicle.display_name}}</div></td>
    </tr>
    <tr>
      <td><div class="doc-label">Заказ-наряд</div><div class="doc-value">№ {{repair_order.number_display}} от {{dates.document_date_display}}</div></td>
      <td><div class="doc-label">Форма оплаты</div><div class="doc-value">{{repair_order.payment_method_label}}</div></td>
      <td><div class="doc-label">Пробег</div><div class="doc-value">{{vehicle.mileage_display}}</div></td>
    </tr>
  </table>
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
  <table class="doc-totals-table">
    <tr><td>Итого работы</td><td>{{totals.works_display}}</td></tr>
    <tr><td>Итого материалы</td><td>{{totals.materials_display}}</td></tr>
    <tr><td>Стоимость заказ-наряда</td><td>{{totals.subtotal_display}}</td></tr>
    {{#totals.has_taxes}}<tr><td>Налоги и сборы</td><td>{{totals.taxes_display}}</td></tr>{{/totals.has_taxes}}
    <tr><td>Итого по заказ-наряду</td><td>{{totals.grand_display}}</td></tr>
    {{#totals.has_prepayment}}<tr><td>Предоплата</td><td>{{totals.prepayment_display}}</td></tr>{{/totals.has_prepayment}}
    <tr class="doc-totals-table__grand"><td>К доплате</td><td>{{totals.due_display}}</td></tr>
  </table>
  <div class="doc-invoice-words">Сумма прописью: <strong>{{totals.due_words_display}}</strong></div>
  <section class="doc-section doc-section--warranty doc-section--warranty-summary">
    <h2 class="doc-section__title">Ключевые условия</h2>
    <div class="doc-terms doc-terms--compact">{{{repair_order.terms_summary_html}}}</div>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Подписи сторон</h2>
    <table class="doc-signatures-table">
      <tr>
        <td>
          <div class="doc-signatures__role">Исполнитель</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signature-caption">{{service.company_name}}</div>
        </td>
        <td>
          <div class="doc-signatures__role">Заказчик</div>
          <div class="doc-signature-line">&nbsp;</div>
          <div class="doc-signature-caption">Работы принял, претензий не имею</div>
        </td>
      </tr>
    </table>
  </section>
</div>
            """,
        ),
        _record(
            "parts_sale",
            "standard",
            "Продажа запчастей без автомобиля",
            """
<div class="document-page">
  <table class="doc-head-table">
    <tr>
      <td class="doc-head-table__left">
        <table class="doc-head-table" style="margin-bottom:0;">
          <tr>
            <td style="width:96px; vertical-align:top; padding-right:12px;">
              <div class="doc-brand-mark">
                {{#service.brand_logo_data_uri}}<img src="{{service.brand_logo_data_uri}}" width="70" height="70" style="width:70px;height:70px;" alt="AutoStop">{{/service.brand_logo_data_uri}}
                {{^service.brand_logo_data_uri}}<div class="doc-brand-mark__fallback">AutoStop</div>{{/service.brand_logo_data_uri}}
              </div>
            </td>
            <td style="vertical-align:top;">
              <div class="doc-kicker">Запасные части</div>
              <h1 class="doc-title">Продажа запчастей</h1>
              <div class="doc-subtitle">Документ № {{repair_order.number_display}} от {{dates.document_date_display}}</div>
            </td>
          </tr>
        </table>
      </td>
      <td class="doc-head-table__right">
        <div class="doc-service">
          <div class="doc-service__name">{{service.company_name}}</div>
          <div class="doc-service__meta">{{service.legal_name}}</div>
          <div class="doc-service__meta">Запчасти: {{service.spare_parts_phone}}</div>
          <div class="doc-service__meta">{{service.website}}</div>
        </div>
      </td>
    </tr>
  </table>
  <section class="doc-grid">
    <div class="doc-card doc-card--wide"><div class="doc-label">Покупатель</div><div class="doc-value">{{parts_sale.buyer_display}} · {{client.phone_display}}</div></div>
    <div class="doc-card doc-card--wide"><div class="doc-label">Продавец</div><div class="doc-value">{{service.legal_name}}, ИНН {{service.inn}}, {{service.address}}</div></div>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Запчасти / материалы</h2>
    <table class="doc-table"><colgroup><col><col style="width: 12%"><col style="width: 16%"><col style="width: 16%"></colgroup><thead><tr><th>Наименование</th><th class="doc-table__narrow">Кол-во</th><th class="doc-table__sum">Цена</th><th class="doc-table__sum">Сумма</th></tr></thead><tbody>
      {{#parts_sale_items}}<tr><td>{{name}}</td><td class="doc-table__narrow">{{quantity_display}}</td><td class="doc-table__sum">{{price_display}}</td><td class="doc-table__sum">{{total_display}}</td></tr>{{/parts_sale_items}}
      {{^parts_sale_items}}<tr><td class="doc-table__empty" colspan="4">Запчасти и материалы не указаны</td></tr>{{/parts_sale_items}}
    </tbody><tfoot><tr><td colspan="3">Итого</td><td class="doc-table__sum">{{totals.materials_display}}</td></tr></tfoot></table>
  </section>
  <section class="doc-totals">
    <div class="doc-totals__row"><span>Товары</span><span>{{totals.materials_display}}</span></div>
    {{#totals.has_prepayment}}<div class="doc-totals__row"><span>Оплачено</span><span>{{totals.prepayment_display}}</span></div>{{/totals.has_prepayment}}
    <div class="doc-totals__row doc-totals__row--grand"><span>К оплате</span><span>{{totals.materials_display}}</span></div>
  </section>
  <section class="doc-section">
    <h2 class="doc-section__title">Условия продажи</h2>
    <div class="doc-terms">{{{parts_sale.terms_html}}}</div>
  </section>
  <section class="doc-signatures">
    <div class="doc-signatures__item"><div class="doc-signatures__role">Продавец</div><div class="doc-signature-line">&nbsp;</div><div class="doc-signature-caption">{{service.company_name}}</div></div>
    <div class="doc-signatures__item"><div class="doc-signatures__role">Покупатель</div><div class="doc-signature-line">&nbsp;</div><div class="doc-signature-caption">Товар получил, претензий по комплектности не имею</div></div>
  </section>
</div>
            """,
        ),
    )
