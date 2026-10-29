"""Approved v6 repair order: shared browser and native print layout."""

# The default template ID remains stable; these classes and named page are isolated.
REPAIR_ORDER_V6_STYLES = """
.document-page.repair-order-v6 { font-family: Arial, 'Segoe UI', sans-serif; font-size: 14px; line-height: 1.35; color: #23272c; padding: 14mm 14mm 16mm; border: 0; }
.repair-order-v6 * { box-sizing: border-box; }
.repair-order-v6 h1, .repair-order-v6 h2, .repair-order-v6 p { margin-top: 0; }
.ro-v6-header { display: grid; grid-template-columns: minmax(0, 1fr) 218px; gap: 16px; padding-bottom: 12px; border-bottom: 1.5px solid #9e2430; margin-bottom: 12px; }
.ro-v6-brand { display: flex; gap: 12px; align-items: center; }
.ro-v6-logo { width: 59px; height: 59px; flex: 0 0 59px; }
.ro-v6-logo img { display: block; width: 59px; height: 59px; object-fit: contain; }
.ro-v6-title { font-size: 26px; line-height: 1.1; font-weight: 700; letter-spacing: -.015em; margin: 0 0 8px; }
.ro-v6-reference { font-size: 12px; line-height: 1.35; color: #555c64; }
.ro-v6-business { text-align: right; }
.ro-v6-business-name { font-size: 17px; font-weight: 700; margin-bottom: 4px; }
.ro-v6-business-address { font-size: 11px; line-height: 1.35; color: #555c64; }
.ro-v6-reception { font-size: 11px; margin-top: 4px; color: #555c64; }
.ro-v6-reception strong { display: block; color: #23272c; font-size: 17px; line-height: 1.2; margin-top: 4px; }
.ro-v6-identity { display: grid; grid-template-columns: 1fr 1.15fr; gap: 16px; background: #f5f6f7; padding: 12px 10px; border: .75px solid #cfd4d9; margin-bottom: 12px; }
.ro-v6-field-label { font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: #555c64; margin-bottom: 4px; }
.ro-v6-field-primary { font-size: 17px; font-weight: 700; line-height: 1.25; margin-bottom: 4px; overflow-wrap: anywhere; }
.ro-v6-field-line { font-size: 14px; line-height: 1.35; }
.ro-v6-field-line span { font-size: 11px; color: #555c64; margin-right: 4px; }
.ro-v6-vehicle { border-left: .75px solid #cfd4d9; padding-left: 16px; }
.ro-v6-vehicle-row { display: flex; flex-wrap: wrap; column-gap: 16px; margin-bottom: 2px; }
.ro-v6-vin { font-variant-numeric: tabular-nums; letter-spacing: .02em; }
.ro-v6-notes { margin-bottom: 16px; }
.ro-v6-note-row { display: grid; grid-template-columns: 144px minmax(0, 1fr); gap: 16px; padding: 8px 0; border-bottom: .75px solid #cfd4d9; break-inside: avoid; }
.ro-v6-note-label { font-size: 12px; font-weight: 700; line-height: 1.35; }
.ro-v6-note-value { font-size: 14px; line-height: 1.35; overflow-wrap: anywhere; }
.ro-v6-section { margin-bottom: 16px; }
.ro-v6-section-title { font-size: 17px; font-weight: 700; line-height: 1.25; margin: 0 0 8px; break-after: avoid; page-break-after: avoid; }
.ro-v6-lines { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 14px; line-height: 1.35; }
.ro-v6-lines thead { display: table-header-group; }
.ro-v6-lines tfoot { display: table-row-group; }
.ro-v6-lines th { background: #f5f6f7; border-top: .75px solid #cfd4d9; border-bottom: .75px solid #cfd4d9; padding: 6px 10px; text-align: left; font-size: 11px; font-weight: 700; }
.ro-v6-lines td { border-bottom: .75px solid #cfd4d9; padding: 8px 10px; vertical-align: top; }
.ro-v6-lines tbody tr { break-inside: avoid; page-break-inside: avoid; }
.ro-v6-lines .ro-v6-number { text-align: right; font-variant-numeric: tabular-nums; }
.ro-v6-lines tbody .ro-v6-row-total { font-weight: 400; }
.ro-v6-lines td:first-child { overflow-wrap: anywhere; }
.ro-v6-lines tfoot td { background: #f5f6f7; font-weight: 700; border-bottom: .75px solid #cfd4d9; }
.ro-v6-money-note { display: block; font-size: 11px; font-weight: 400; line-height: 1.25; margin-top: 4px; white-space: normal; }
.ro-v6-empty { color: #555c64; }
.ro-v6-totals { width: calc(100% - 160px); margin: 16px 0 0 auto; border-collapse: collapse; table-layout: auto; font-variant-numeric: tabular-nums; break-inside: avoid; page-break-inside: avoid; }
.ro-v6-totals td { padding: 8px 10px; border: .75px solid #cfd4d9; font-size: 14px; line-height: 1.35; vertical-align: middle; }
.ro-v6-totals td:last-child { text-align: right; font-size: 14px; font-weight: 700; white-space: nowrap; }
.ro-v6-totals small { font-size: 11px; color: #555c64; }
.ro-v6-totals .ro-v6-total-due td { background: #eef0f2; font-size: 17px; font-weight: 700; }
.ro-v6-totals .ro-v6-total-due td:last-child { font-size: 18px; }
.ro-v6-totals .ro-v6-total-due-first td { border-top: 1.25px solid #555c64; }
.ro-v6-terms-header { padding-bottom: 6px; border-bottom: 1.5px solid #9e2430; margin-bottom: 8px; }
.ro-v6-terms-title { font-size: 26px; line-height: 1.2; margin: 0 0 6px; font-weight: 700; letter-spacing: -.015em; }
.ro-v6-terms-meta { display: flex; flex-wrap: wrap; column-gap: 16px; row-gap: 2px; align-items: baseline; }
.ro-v6-terms-identity { font-size: 12px; line-height: 1.35; color: #555c64; }
.ro-v6-terms-copy { font-size: 13.6px; line-height: 1.26; }
.ro-v6-terms-copy .doc-terms__lead { font-size: 13.6px; line-height: 1.26; color: #555c64; margin: 0 0 6px; }
.ro-v6-terms-copy .doc-terms__list { display: block; column-count: 2; column-gap: 27px; column-fill: balance; list-style: none; counter-reset: condition; margin: 0; padding: 0; }
.ro-v6-terms-copy .doc-terms__list li { position: relative; counter-increment: condition; padding: 5px 0 0; margin: 0 0 7px; border-top: .75px solid #cfd4d9; break-inside: avoid; page-break-inside: avoid; }
.ro-v6-terms-copy .doc-terms__list li::before { content: counter(condition, decimal-leading-zero); position: absolute; top: 7px; left: 0; font-size: 11px; line-height: 1.2; font-weight: 700; color: #9e2430; }
.ro-v6-terms-copy .doc-terms__list strong { display: block; font-size: 14px; line-height: 1.25; color: #23272c; margin-bottom: 4px; padding-left: 25px; }
.ro-v6-terms-copy .doc-terms__paragraph { margin: 0; }
.ro-v6-terms-copy .doc-terms__paragraph + .doc-terms__paragraph { margin-top: 4px; }
.ro-v6-signatures { display: grid; grid-template-columns: minmax(0, 1fr) 40mm minmax(0, 1fr); gap: 8mm; align-items: center; border-top: .75px solid #cfd4d9; padding-top: 8px; margin-top: 12px; break-inside: avoid; page-break-inside: avoid; }
.ro-v6-signature-label { font-size: 14px; font-weight: 700; }
.ro-v6-signature-line { height: 31px; border-bottom: .75px solid #555c64; }
@page repair-order-v6 { margin: 9mm 9mm 12mm; @bottom-left { content: 'AutoStop'; font-family: Arial; font-size: 7.5pt; color: #555c64; vertical-align: top; padding-left: 5mm; } @bottom-right { content: 'Страница ' counter(page) ' из ' counter(pages); font-family: Arial; font-size: 7.5pt; color: #555c64; vertical-align: top; padding-right: 5mm; } }
@media print { .document-page.repair-order-v6 { page: repair-order-v6; width: auto; min-height: 0; margin: 0; border: 0; box-shadow: none; padding: 5mm 5mm 4mm; } }

.ro-v6-lines th:nth-child(2), .ro-v6-lines tbody td:nth-child(2) { text-align: center; }
.ro-v6-field-line .ro-v6-license { font-size: inherit; color: #23272c; font-weight: 700; letter-spacing: .025em; text-transform: uppercase; margin-right: 0; }
.ro-v6-terms-identity .ro-v6-license { text-transform: uppercase; letter-spacing: .02em; }
.ro-v6-signature-caption { font-size: 11px; line-height: 1.3; color: #555c64; margin-top: 4px; }
.ro-v6-stamp-space { width: 40mm; height: 40mm; border: .75px dashed #cfd4d9; border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: 11px; color: #767e87; }
.document-page.repair-order-v6.ro-v6-terms-page { padding-bottom: 13mm; }
@media print { .document-page.repair-order-v6.ro-v6-terms-page { padding-bottom: 1mm; } }
.ro-v6-header > *, .ro-v6-identity > * { min-width: 0; }
.ro-v6-business, .ro-v6-field-line, .ro-v6-terms-meta { overflow-wrap: anywhere; }

""".strip()

REPAIR_ORDER_V6_TEMPLATE = """
<div class="document-page repair-order-v6">
<header class="ro-v6-header">
  <div class="ro-v6-brand">
    <div class="ro-v6-logo">{{#service.brand_logo_data_uri}}<img src="{{service.brand_logo_data_uri}}" alt="AutoStop">{{/service.brand_logo_data_uri}}{{^service.brand_logo_data_uri}}<strong>AutoStop</strong>{{/service.brand_logo_data_uri}}</div>
    <div><h1 class="ro-v6-title">Заказ-наряд</h1><div class="ro-v6-reference">№ {{repair_order.number_display}} от {{dates.document_date_display}}</div></div>
  </div>
  <div class="ro-v6-business"><div class="ro-v6-business-name">{{service.company_name}}</div><div class="ro-v6-business-address">{{service.address}}</div><div class="ro-v6-reception">Телефон приёмки<strong>{{service.reception_phone_display}}</strong></div></div>
</header>
<section class="ro-v6-identity">
  <div><div class="ro-v6-field-label">Клиент</div><div class="ro-v6-field-primary">{{client.name_display}}</div><div class="ro-v6-field-line"><span>Телефон</span>{{client.phone_display}}</div></div>
  <div class="ro-v6-vehicle"><div class="ro-v6-field-label">Автомобиль</div><div class="ro-v6-field-primary">{{vehicle.display_name}}</div><div class="ro-v6-vehicle-row"><div class="ro-v6-field-line"><span>Госномер</span><span class="ro-v6-license">{{vehicle.license_plate_display}}</span></div><div class="ro-v6-field-line"><span>Пробег</span>{{vehicle.mileage_display}}</div></div><div class="ro-v6-field-line ro-v6-vin"><span>VIN</span>{{vehicle.vin_display}}</div></div>
</section>
<section class="ro-v6-notes">
  <div class="ro-v6-note-row"><div class="ro-v6-note-label">Причина обращения</div><div class="ro-v6-note-value">{{{repair_order.reason_html}}}</div></div>
  <div class="ro-v6-note-row"><div class="ro-v6-note-label">Информация для клиента</div><div class="ro-v6-note-value">{{{repair_order.client_information_html}}}</div></div>
</section>
<section class="ro-v6-section">
  <h2 class="ro-v6-section-title">Работы</h2>
  <table class="ro-v6-lines"><colgroup><col style="width:55%"><col style="width:11%"><col style="width:17%"><col style="width:17%"></colgroup><thead><tr><th>Наименование</th><th class="ro-v6-number">Кол-во</th><th class="ro-v6-number">Цена</th><th class="ro-v6-number">Сумма</th></tr></thead><tbody>
  {{#works}}<tr><td>{{name}}</td><td class="ro-v6-number">{{quantity_display}}</td><td class="ro-v6-number">{{price_display}}</td><td class="ro-v6-number ro-v6-row-total">{{total_display}}{{#has_rounding_adjustment}}<small class="ro-v6-money-note">Округление {{rounding_adjustment_display}} руб.</small>{{/has_rounding_adjustment}}</td></tr>{{/works}}
  {{^works}}<tr><td colspan="4" class="ro-v6-empty">Работы не указаны</td></tr>{{/works}}
  </tbody><tfoot><tr><td colspan="3">Итого работы</td><td class="ro-v6-number">{{totals.works_display}}</td></tr></tfoot></table>
</section>
<section class="ro-v6-section">
  <h2 class="ro-v6-section-title">Материалы / запчасти</h2>
  <table class="ro-v6-lines"><colgroup><col style="width:55%"><col style="width:11%"><col style="width:17%"><col style="width:17%"></colgroup><thead><tr><th>Наименование</th><th class="ro-v6-number">Кол-во</th><th class="ro-v6-number">Цена</th><th class="ro-v6-number">Сумма</th></tr></thead><tbody>
  {{#materials}}<tr><td>{{name}}</td><td class="ro-v6-number">{{quantity_display}}</td><td class="ro-v6-number">{{price_display}}</td><td class="ro-v6-number ro-v6-row-total">{{total_display}}{{#has_rounding_adjustment}}<small class="ro-v6-money-note">Округление {{rounding_adjustment_display}} руб.</small>{{/has_rounding_adjustment}}</td></tr>{{/materials}}
  {{^materials}}<tr><td colspan="4" class="ro-v6-empty">Материалы не указаны</td></tr>{{/materials}}
  </tbody><tfoot><tr><td colspan="3">Итого материалы</td><td class="ro-v6-number">{{totals.materials_display}}</td></tr></tfoot></table>
</section>
<table class="ro-v6-totals"><colgroup><col style="width:74%"><col style="width:26%"></colgroup>
  <tr><td>Стоимость заказ-наряда за наличный расчет</td><td>{{totals.base_total_ruble_display}}</td></tr>
  <tr><td>Стоимость заказ-наряда по безналичному расчету<br><small>включая налоги и сборы 15%</small></td><td>{{totals.noncash_total_ruble_display}}</td></tr>
  {{#totals.has_cash_like_prepayment}}<tr><td>Предоплата за наличные</td><td>{{totals.cash_like_prepayment_ruble_display}}</td></tr>{{/totals.has_cash_like_prepayment}}
  {{#totals.has_cashless_prepayment}}<tr><td>Предоплата по безналу</td><td>{{totals.cashless_prepayment_ruble_display}}</td></tr>{{/totals.has_cashless_prepayment}}
  <tr class="ro-v6-total-due ro-v6-total-due-first"><td>Доплата по безналичному расчету</td><td>{{totals.noncash_due_ruble_display}}</td></tr>
  <tr class="ro-v6-total-due"><td>Доплата по наличному расчету</td><td>{{totals.cash_due_ruble_display}}</td></tr>
</table>
</div>
<!-- AUTOSTOPCRM_PAGE_BREAK -->
<div class="document-page repair-order-v6 ro-v6-terms-page">
<header class="ro-v6-terms-header"><h1 class="ro-v6-terms-title">Гарантийные и важные условия</h1><div class="ro-v6-terms-meta"><div class="ro-v6-reference">К заказ-наряду № {{repair_order.number_display}} от {{dates.document_date_display}}</div><div class="ro-v6-terms-identity">{{vehicle.display_name}} · <span class="ro-v6-license">{{vehicle.license_plate_display}}</span> · {{client.name_display}}</div></div></header>
<section class="ro-v6-terms-copy">{{{repair_order.warranty_terms_v6_html}}}</section>
<section class="ro-v6-signatures"><div><div class="ro-v6-signature-label">Администратор</div><div class="ro-v6-signature-line"></div><div class="ro-v6-signature-caption">Подпись</div></div><div class="ro-v6-stamp-space">М. П.</div><div><div class="ro-v6-signature-label">Клиент</div><div class="ro-v6-signature-line"></div><div class="ro-v6-signature-caption">Подпись</div></div></section>
</div>
""".strip()
