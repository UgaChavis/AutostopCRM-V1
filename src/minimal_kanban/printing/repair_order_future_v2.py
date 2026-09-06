"""Future repair-order layout kept available without changing the active template."""

from __future__ import annotations

from typing import Any


def build_pages(
    works: list[dict[str, Any]], materials: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Paginate the saved future layout while keeping row numbering continuous."""

    def numbered_rows(rows: list[dict[str, Any]], *, start_index: int) -> list[dict[str, Any]]:
        return [{**row, "display_index": start_index + offset} for offset, row in enumerate(rows)]

    def chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
        return [rows[index : index + size] for index in range(0, len(rows), size)]

    work_chunks = [works[:25]] + chunks(works[25:], 43)
    material_chunks = chunks(materials, 43) or [[]]
    if len(material_chunks) > 1 and len(material_chunks[-1]) < 20:
        tail = material_chunks[-2] + material_chunks[-1]
        split_at = (len(tail) + 1) // 2
        material_chunks[-2:] = [tail[:split_at], tail[split_at:]]
    elif len(material_chunks[-1]) > 39:
        tail = material_chunks.pop()
        split_at = (len(tail) + 1) // 2
        material_chunks.extend((tail[:split_at], tail[split_at:]))

    pages: list[dict[str, Any]] = []
    next_display_index = 1
    for index, rows in enumerate(work_chunks):
        page_rows = numbered_rows(rows, start_index=next_display_index)
        next_display_index += len(rows)
        pages.append(
            {
                "is_first": index == 0,
                "is_items": True,
                "is_works": True,
                "is_materials": False,
                "is_terms": False,
                "rows": page_rows,
                "has_rows": bool(rows),
                "show_total": index == len(work_chunks) - 1,
                "section_title": "Работы",
                "section_caption": (
                    f"позиции {page_rows[0]['display_index']}–{page_rows[-1]['display_index']}"
                    if page_rows
                    else "позиции не указаны"
                ),
            }
        )

    for index, rows in enumerate(material_chunks):
        page_rows = numbered_rows(rows, start_index=next_display_index)
        next_display_index += len(rows)
        pages.append(
            {
                "is_first": False,
                "is_items": True,
                "is_works": False,
                "is_materials": True,
                "is_terms": False,
                "rows": page_rows,
                "has_rows": bool(rows),
                "show_total": index == len(material_chunks) - 1,
                "show_payments": index == len(material_chunks) - 1,
                "section_title": "Материалы / запчасти",
                "section_caption": (
                    f"позиции {page_rows[0]['display_index']}–{page_rows[-1]['display_index']}"
                    if page_rows
                    else "позиции не указаны"
                ),
            }
        )

    pages.append(
        {
            "is_first": False,
            "is_items": False,
            "is_works": False,
            "is_materials": False,
            "is_terms": True,
            "rows": [],
            "has_rows": False,
            "show_total": False,
            "show_payments": False,
        }
    )
    page_count = len(pages)
    for index, page in enumerate(pages):
        page["page_number"] = index + 1
        page["page_count"] = page_count
        page["has_page_break_after"] = index < page_count - 1
    return pages


FUTURE_REPAIR_ORDER_V2_STYLES = r"""
  .future-ro {
    --future-ro-ink: #172235;
    --future-ro-navy: #14284a;
    --future-ro-blue: #234f80;
    --future-ro-soft-blue: #eef3f8;
    --future-ro-line: #d5dee8;
    --future-ro-muted: #647187;
    position: relative;
    padding: 9mm 10mm 11mm;
    color: var(--future-ro-ink);
    font-family: Arial, "Segoe UI", sans-serif;
    font-size: 9pt;
    line-height: 1.35;
    font-variant-numeric: tabular-nums;
  }
  .future-ro__content { min-width: 0; }
  .future-ro__hero {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 62mm 14mm;
    gap: 5mm;
    align-items: start;
    margin-bottom: 3mm;
  }
  .future-ro__brand {
    display: flex;
    align-items: center;
    gap: 4mm;
    min-width: 0;
  }
  .future-ro__logo {
    flex: 0 0 auto;
    width: 21mm;
    height: 21mm;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .future-ro__logo img { width: 21mm; height: 21mm; object-fit: contain; }
  .future-ro__logo-fallback {
    width: 19mm;
    height: 19mm;
    border: 1.4mm solid #e33434;
    border-radius: 50%;
    display: grid;
    place-items: center;
    color: var(--future-ro-navy);
    font-size: 6.5pt;
    font-weight: 800;
    text-transform: uppercase;
  }
  .future-ro__kicker {
    margin-bottom: 1.2mm;
    color: #65748a;
    font-size: 7pt;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .future-ro__title {
    margin: 0;
    color: var(--future-ro-navy);
    font-size: 24pt;
    font-weight: 800;
    letter-spacing: -0.025em;
    line-height: 1;
  }
  .future-ro__subtitle {
    margin-top: 2mm;
    color: #31567f;
    font-size: 11pt;
    font-weight: 700;
  }
  .future-ro__service { padding-top: 1mm; text-align: right; }
  .future-ro__service-name {
    color: var(--future-ro-navy);
    font-size: 16pt;
    font-weight: 800;
  }
  .future-ro__service-meta {
    margin-top: 1.2mm;
    color: #536278;
    font-size: 7.5pt;
    line-height: 1.35;
  }
  .future-ro__page-number {
    justify-self: end;
    width: 12mm;
    height: 12mm;
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: var(--future-ro-soft-blue);
    color: #234e7d;
    font-size: 7.3pt;
    font-weight: 800;
  }
  .future-ro__reception {
    display: grid;
    grid-template-columns: 44mm 1fr;
    align-items: center;
    margin-bottom: 3.5mm;
    padding: 2.5mm 4mm;
    border-radius: 3mm;
    background: linear-gradient(90deg, #173968, #275e9f);
    color: #fff;
  }
  .future-ro__reception span {
    display: block;
    font-size: 6.5pt;
    font-weight: 700;
    letter-spacing: 0.12em;
    opacity: 0.82;
    text-transform: uppercase;
  }
  .future-ro__reception b { display: block; font-size: 15pt; line-height: 1.1; }
  .future-ro__reception-copy {
    padding-left: 5mm;
    border-left: 1px solid rgba(255, 255, 255, 0.3);
    font-size: 8pt;
    line-height: 1.35;
  }
  .future-ro__meta {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    overflow: hidden;
    margin-bottom: 3.5mm;
    border: 1px solid #d8e0e9;
    border-radius: 3mm;
  }
  .future-ro__meta-cell {
    min-height: 14mm;
    padding: 2.4mm 3mm;
    border-right: 1px solid #d8e0e9;
    border-bottom: 1px solid #d8e0e9;
    background: #fbfcfe;
  }
  .future-ro__meta-cell:nth-child(3n) { border-right: 0; }
  .future-ro__meta-cell:nth-last-child(-n + 3) { border-bottom: 0; }
  .future-ro__meta-cell span {
    display: block;
    margin-bottom: 1mm;
    color: #6d798a;
    font-size: 6.2pt;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .future-ro__meta-cell b {
    display: block;
    overflow-wrap: anywhere;
    color: #18263b;
    font-size: 9pt;
  }
  .future-ro__info {
    display: grid;
    grid-template-columns: 1.45fr 1fr;
    gap: 3mm;
    margin-bottom: 3.5mm;
  }
  .future-ro__info-card {
    min-height: 18mm;
    padding: 2.4mm 3mm;
    border: 1px solid #dde4ec;
    border-radius: 2.5mm;
    background: #fff;
  }
  .future-ro__info-card--soft { background: #f5f8fb; }
  .future-ro__info-card h2 {
    margin: 0 0 1.1mm;
    color: #2b527c;
    font-size: 7.4pt;
    font-weight: 800;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .future-ro__info-card p { margin: 0; font-size: 7.4pt; line-height: 1.35; }
  .future-ro__table {
    width: 100%;
    margin: 0 0 3mm;
    overflow: hidden;
    border: 1px solid var(--future-ro-line);
    border-collapse: separate;
    border-radius: 2mm;
    border-spacing: 0;
    table-layout: fixed;
  }
  .future-ro__table col:nth-child(1) { width: 6%; }
  .future-ro__table col:nth-child(2) { width: 55%; }
  .future-ro__table col:nth-child(3) { width: 10%; }
  .future-ro__table col:nth-child(4) { width: 14%; }
  .future-ro__table col:nth-child(5) { width: 15%; }
  .future-ro__section-row th {
    padding: 2.6mm 0 2mm;
    border: 0;
    background: #fff;
    color: var(--future-ro-navy);
    text-align: left;
    text-transform: none;
  }
  .future-ro__section-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 4mm;
  }
  .future-ro__section-title { display: flex; align-items: center; gap: 2.5mm; }
  .future-ro__section-rule {
    width: 1.4mm;
    height: 6mm;
    border-radius: 1mm;
    background: #e33434;
  }
  .future-ro__section-heading h2 {
    margin: 0;
    color: #172b4d;
    font-size: 12.5pt;
    font-weight: 800;
    line-height: 1;
  }
  .future-ro__section-caption {
    color: #69778a;
    font-size: 7pt;
    font-weight: 400;
  }
  .future-ro__columns th {
    padding: 1.45mm 1.25mm;
    border: 0;
    background: var(--future-ro-blue);
    color: #fff;
    font-size: 6.4pt;
    font-weight: 700;
    letter-spacing: 0.055em;
    text-align: left;
    text-transform: uppercase;
  }
  .future-ro__columns th:first-child,
  .future-ro__columns th:nth-child(3) { text-align: center; }
  .future-ro__columns th:nth-child(4),
  .future-ro__columns th:nth-child(5) { text-align: right; }
  .future-ro__table td {
    padding: 1.12mm 1.25mm;
    border: 0;
    border-top: 1px solid #e3e8ee;
    vertical-align: middle;
    font-size: 7.2pt;
    line-height: 1.14;
  }
  .future-ro__table--materials td {
    padding-top: 0.95mm;
    padding-bottom: 0.95mm;
    font-size: 7pt;
    line-height: 1.1;
  }
  .future-ro__table tbody tr:nth-child(even) td { background: #f7f9fb; }
  .future-ro__row-number {
    color: #758195;
    font-size: 6.4pt;
    text-align: center;
  }
  .future-ro__quantity { text-align: center; }
  .future-ro__money { white-space: nowrap; text-align: right; }
  .future-ro__sum { color: #142f53; font-weight: 700; }
  .future-ro__empty td { color: #758195; text-align: center; }
  .future-ro__table tfoot td {
    padding: 2mm 1.25mm;
    border-top: 1px solid #cbd8e5;
    background: #eaf1f8;
    color: #163a65;
    font-size: 8pt;
    font-weight: 800;
  }
  .future-ro__table tfoot td:first-child { text-align: right; }
  .future-ro__payments {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 3mm;
    margin-top: 2mm;
    break-inside: avoid;
  }
  .future-ro__payment {
    padding: 3mm 3.5mm;
    border: 1px solid #cfd9e5;
    border-radius: 3mm;
    background: #f7f9fc;
  }
  .future-ro__payment--accent {
    border-color: #1e528a;
    background: linear-gradient(135deg, #173c69, #2b68a6);
    color: #fff;
  }
  .future-ro__payment span {
    display: block;
    color: #5f6e83;
    font-size: 6.8pt;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .future-ro__payment--accent span { color: rgba(255, 255, 255, 0.82); }
  .future-ro__payment b {
    display: block;
    margin-top: 1.6mm;
    color: #173b65;
    font-size: 14pt;
  }
  .future-ro__payment--accent b { color: #fff; }
  .future-ro__payment small {
    display: block;
    margin-top: 1mm;
    color: rgba(255, 255, 255, 0.8);
    font-size: 6.5pt;
  }
  .future-ro__compact-head {
    display: grid;
    grid-template-columns: 1.05fr 1fr 16mm;
    gap: 5mm;
    align-items: center;
    min-height: 17mm;
    margin-bottom: 3mm;
    padding-bottom: 3mm;
    border-bottom: 1px solid #d4dde7;
  }
  .future-ro__compact-brand { display: flex; align-items: center; gap: 2.5mm; }
  .future-ro__compact-logo {
    width: 11mm;
    height: 11mm;
    object-fit: contain;
  }
  .future-ro__compact-copy,
  .future-ro__compact-order { display: flex; flex-direction: column; gap: 0.7mm; }
  .future-ro__compact-copy b { color: #17345c; font-size: 10.5pt; }
  .future-ro__compact-head span { color: var(--future-ro-muted); font-size: 6.8pt; }
  .future-ro__compact-order { text-align: right; }
  .future-ro__compact-order b { color: #223550; font-size: 8pt; }
  .future-ro__compact-page {
    justify-self: end;
    width: 12mm;
    height: 12mm;
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: var(--future-ro-soft-blue);
    color: #234e7d;
    font-size: 7.3pt;
    font-weight: 800;
  }
  .future-ro__terms-page { display: flex; flex-direction: column; }
  .future-ro__terms-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 4mm;
    margin-bottom: 4mm;
  }
  .future-ro__terms-heading > div { display: flex; align-items: center; gap: 2.5mm; }
  .future-ro__terms-heading h2 {
    margin: 0;
    color: #172b4d;
    font-size: 12.5pt;
    font-weight: 800;
  }
  .future-ro__terms-heading span:last-child { color: #69778a; font-size: 7pt; }
  .future-ro__terms .doc-terms__lead { display: none; }
  .future-ro__terms .doc-terms__list {
    display: block;
    margin: 0;
    padding-left: 5mm;
    columns: 2;
    column-gap: 7mm;
  }
  .future-ro__terms .doc-terms__list li {
    margin: 0 0 2.5mm;
    padding: 0 0 2.5mm 0.8mm;
    border-bottom: 1px solid #dce4ec;
    break-inside: avoid;
    color: #27364b;
    font-size: 8.15pt;
    line-height: 1.36;
  }
  .future-ro__terms .doc-terms__list strong { color: #173d69; }
  .future-ro__signatures {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 5mm;
    margin-top: auto;
    padding-top: 4mm;
    border-top: 1px solid #ced8e3;
    break-inside: avoid;
  }
  .future-ro__signature {
    min-height: 34mm;
    padding: 3mm;
    border: 1px solid #d8e1ea;
    border-radius: 2.5mm;
    background: #fbfcfe;
  }
  .future-ro__signature h3 { margin: 0; color: #243b5a; font-size: 8pt; }
  .future-ro__signature-fields {
    display: grid;
    grid-template-columns: 1.5fr 0.75fr 0.62fr;
    gap: 2.5mm;
    margin-top: 8mm;
  }
  .future-ro__signature-field {
    padding-top: 1mm;
    border-top: 1px solid #5c6878;
    color: #7b8492;
    font-size: 5.8pt;
  }
  .future-ro__signature-note,
  .future-ro__signature-seal {
    margin-top: 2.5mm;
    color: #59677a;
    font-size: 6.4pt;
    line-height: 1.25;
  }
  .future-ro__footer {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 5mm;
    margin-top: 5mm;
    padding-top: 2mm;
    border-top: 1px solid #d9e0e8;
    color: #7a8595;
    font-size: 5.8pt;
  }
  .future-ro__footer b { color: #53647a; font-size: 6pt; text-align: right; }
  @media print {
    .future-ro.document-page {
      page: future-repair-order-v2;
      min-height: 0;
      padding: 0;
    }
    .future-ro__table thead { display: table-header-group; }
    .future-ro__table tfoot { display: table-row-group; }
    .future-ro__table tr { break-inside: avoid; page-break-inside: avoid; }
    .future-ro + .future-ro { break-before: page; page-break-before: always; }
    .future-ro__terms-page { min-height: 270mm; }
  }
  @page future-repair-order-v2 { size: A4 portrait; margin: 9mm 10mm; }
""".strip()


FUTURE_REPAIR_ORDER_V2_TEMPLATE = r"""
{{#future_repair_order_v2.pages}}
<div class="document-page future-ro{{#is_terms}} future-ro__terms-page{{/is_terms}}">
  {{#is_first}}
  <header class="future-ro__hero">
    <div class="future-ro__brand">
      <div class="future-ro__logo">
        {{#service.brand_logo_data_uri}}<img src="{{service.brand_logo_data_uri}}" alt="AutoStop">{{/service.brand_logo_data_uri}}
        {{^service.brand_logo_data_uri}}<div class="future-ro__logo-fallback">AutoStop</div>{{/service.brand_logo_data_uri}}
      </div>
      <div>
        <div class="future-ro__kicker">Автотехцентр №1 · печатная форма</div>
        <h1 class="future-ro__title">Заказ-наряд</h1>
        <div class="future-ro__subtitle">№ {{repair_order.number_display}} от {{dates.document_date_only_display}}</div>
      </div>
    </div>
    <div class="future-ro__service">
      <div class="future-ro__service-name">{{service.company_name}}</div>
      <div class="future-ro__service-meta">{{service.address}}<br>{{service.phone}}</div>
    </div>
    <div class="future-ro__page-number">{{page_number}} / {{page_count}}</div>
  </header>

  <div class="future-ro__reception">
    <div>
      <span>Телефон ресепшена</span>
      <b>{{#service.reception_phone}}{{service.reception_phone}}{{/service.reception_phone}}{{^service.reception_phone}}{{service.phone}}{{/service.reception_phone}}</b>
    </div>
    <div class="future-ro__reception-copy">Приём автомобиля, запись и вопросы по заказ-наряду</div>
  </div>

  <div class="future-ro__meta">
    <div class="future-ro__meta-cell"><span>Клиент</span><b>{{client.name_display}}</b></div>
    <div class="future-ro__meta-cell"><span>Телефон</span><b>{{client.phone_display}}</b></div>
    <div class="future-ro__meta-cell"><span>Автомобиль</span><b>{{vehicle.display_name}}</b></div>
    <div class="future-ro__meta-cell"><span>Госномер</span><b>{{vehicle.license_plate_display}}</b></div>
    <div class="future-ro__meta-cell"><span>VIN</span><b>{{vehicle.vin_display}}</b></div>
    <div class="future-ro__meta-cell"><span>Пробег</span><b>{{vehicle.mileage_display}}</b></div>
  </div>

  <div class="future-ro__info">
    <section class="future-ro__info-card">
      <h2>Причина обращения</h2>
      <p>{{{repair_order.reason_html}}}</p>
    </section>
    <section class="future-ro__info-card future-ro__info-card--soft">
      <h2>Реквизиты сервиса</h2>
      <p>{{service.legal_name}}<br>ИНН {{service.inn}} · БИК {{service.bik}}<br>{{service.address}} · {{service.email}}</p>
    </section>
  </div>
  {{/is_first}}

  {{^is_first}}
  <header class="future-ro__compact-head">
    <div class="future-ro__compact-brand">
      {{#service.brand_logo_data_uri}}<img class="future-ro__compact-logo" src="{{service.brand_logo_data_uri}}" alt="AutoStop">{{/service.brand_logo_data_uri}}
      <div class="future-ro__compact-copy"><b>{{service.company_name}}</b><span>Заказ-наряд №{{repair_order.number_display}} от {{dates.document_date_only_display}}</span></div>
    </div>
    <div class="future-ro__compact-order"><b>{{client.name_display}}</b><span>{{vehicle.display_name}} · госномер {{vehicle.license_plate_display}}</span></div>
    <div class="future-ro__compact-page">{{page_number}} / {{page_count}}</div>
  </header>
  {{/is_first}}

  {{#is_items}}
  <div class="future-ro__content">
    <table class="future-ro__table{{#is_materials}} future-ro__table--materials{{/is_materials}}">
      <colgroup><col><col><col><col><col></colgroup>
      <thead>
        <tr class="future-ro__section-row"><th colspan="5">
          <div class="future-ro__section-heading">
            <div class="future-ro__section-title"><span class="future-ro__section-rule"></span><h2>{{section_title}}</h2></div>
            <span class="future-ro__section-caption">{{section_caption}}</span>
          </div>
        </th></tr>
        <tr class="future-ro__columns"><th>№</th><th>Наименование</th><th>Кол-во</th><th>Цена, ₽</th><th>Сумма, ₽</th></tr>
      </thead>
      <tbody>
        {{#rows}}<tr><td class="future-ro__row-number">{{display_index}}</td><td>{{name}}</td><td class="future-ro__quantity">{{quantity_display}}</td><td class="future-ro__money">{{price_display}}</td><td class="future-ro__money future-ro__sum">{{total_display}}</td></tr>{{/rows}}
        {{^has_rows}}<tr class="future-ro__empty"><td colspan="5">{{#is_works}}Работы{{/is_works}}{{#is_materials}}Материалы{{/is_materials}} не указаны</td></tr>{{/has_rows}}
      </tbody>
      {{#show_total}}<tfoot><tr><td colspan="4">{{#is_works}}Итого работы{{/is_works}}{{#is_materials}}Итого материалы{{/is_materials}}</td><td class="future-ro__money">{{#is_works}}{{totals.works_display}}{{/is_works}}{{#is_materials}}{{totals.materials_display}}{{/is_materials}} ₽</td></tr></tfoot>{{/show_total}}
    </table>

    {{#show_payments}}
    <div class="future-ro__payments">
      <div class="future-ro__payment">
        <span>К оплате наличными</span>
        <b>{{totals.cash_due_ruble_display}}</b>
      </div>
      <div class="future-ro__payment future-ro__payment--accent">
        <span>К оплате по безналичному расчёту</span>
        <b>{{totals.noncash_due_ruble_display}}</b>
        <small>+15%, включая налоги и сборы</small>
      </div>
    </div>
    {{/show_payments}}
  </div>
  {{/is_items}}

  {{#is_terms}}
  <div class="future-ro__terms-heading">
    <div><span class="future-ro__section-rule"></span><h2>Гарантийные и важные условия</h2></div>
    <span>Являются частью заказ-наряда</span>
  </div>
  <div class="future-ro__terms">{{{repair_order.warranty_terms_html}}}</div>

  <div class="future-ro__signatures">
    <section class="future-ro__signature">
      <h3>Администратор / исполнитель</h3>
      <div class="future-ro__signature-fields"><div class="future-ro__signature-field">ФИО</div><div class="future-ro__signature-field">Подпись</div><div class="future-ro__signature-field">Дата</div></div>
      <div class="future-ro__signature-seal">М.П. (при наличии)</div>
    </section>
    <section class="future-ro__signature">
      <h3>Заказчик / представитель</h3>
      <div class="future-ro__signature-fields"><div class="future-ro__signature-field">ФИО</div><div class="future-ro__signature-field">Подпись</div><div class="future-ro__signature-field">Дата</div></div>
      <div class="future-ro__signature-note">С перечнем работ, стоимостью и гарантийными условиями ознакомлен.</div>
    </section>
  </div>
  {{/is_terms}}

  <footer class="future-ro__footer">
    <span>{{service.company_name}} · {{service.address}} · {{service.phone}}</span>
    <b>Заказ-наряд №{{repair_order.number_display}} · {{vehicle.display_name}}</b>
  </footer>
</div>
{{#has_page_break_after}}<!-- AUTOSTOPCRM_PAGE_BREAK -->{{/has_page_break_after}}
{{/future_repair_order_v2.pages}}
""".strip()
