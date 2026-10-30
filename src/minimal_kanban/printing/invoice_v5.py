"""Approved invoice v5: isolated layout and presentation-only context."""

from __future__ import annotations

import math
from typing import Any


def _present(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in {"—", "-"} else text


def _css_string(value: str) -> str:
    # Hex escapes protect both the CSS string and its surrounding HTML style tag.
    return '"' + "".join(f"\\{ord(char):x} " for char in value) + '"'


def _text_width(value: Any, size: float) -> float:
    return sum(size * (0.28 if char in " ,." else 0.56) for char in str(value))


def _column_widths(rows: list[dict[str, Any]]) -> dict[str, str]:
    # Standard v5 proportions stay exact; expand only when canonical values need room.
    content_px = 182 * 96 / 25.4
    widths = [15.0, 14.0, 14.0]
    for row in rows:
        needs = [
            _text_width(row["quantity_display"], 13.5) + 45 + 5 + 14,
            _text_width(row["price_display"], 14.5) + 14,
            _text_width(row["total_display"], 14.5) + 14,
        ]
        widths = [
            max(current, math.ceil(needed / content_px * 1000) / 10)
            for current, needed in zip(widths, needs, strict=True)
        ]
    return {
        key: f"{value:g}"
        for key, value in zip(
            ("column_b_width", "column_c_width", "column_d_width", "column_e_width"),
            (96 - sum(widths), *widths),
            strict=True,
        )
    }


def invoice_v5_context(
    rows: list[dict[str, Any]],
    *,
    service_kpp: str,
    client_kpp: str,
    signer_position: str,
    signer_name: str,
    number_display: str,
    date_display: str,
    amount_due_display: str,
    vehicle: str,
    license_plate: str,
) -> dict[str, Any]:
    previous = ""
    for index, row in enumerate(rows):
        section = row["section"]
        row["v5_group_heading"] = (
            ("Работы" if section == "works" else "Материалы / запчасти")
            if section != previous
            else ""
        )
        row["v5_section_break"] = bool(index and section != previous)
        previous = section
    # Keep the canonical row identities: custom copies use them to verify rounding notes.
    return {
        "has_service_kpp": bool(_present(service_kpp)),
        "has_client_kpp": bool(_present(client_kpp)),
        "signer_position_display": _present(signer_position) or "Руководитель",
        "has_signer_name": bool(_present(signer_name)),
        "vehicle_summary_display": " · ".join(
            value for value in (_present(vehicle), _present(license_plate).upper()) if value
        ),
        "running_reference_css": _css_string(f"Счёт № {number_display} от {date_display}"),
        "settlement_width": str(
            max(310, math.ceil(_text_width(f"{amount_due_display} ₽", 28) + 160))
        ),
        **_column_widths(rows),
    }


INVOICE_V5_STYLES = """
.document-page.invoice-v5 { --inv-v5-ink:#25292e; --inv-v5-muted:#5b626a; --inv-v5-rule:#d1d5da; --inv-v5-light:#f4f5f6; --inv-v5-red:#a32632; font:13.5px/1.4 Arial,'Segoe UI',sans-serif; color:var(--inv-v5-ink); padding:14mm; border:0; }
.invoice-v5 * { box-sizing:border-box; }
.invoice-v5 h1, .invoice-v5 h2, .invoice-v5 p { margin:0; }
.inv-v5-logo-fallback { font-size:12px; }
.inv-v5-masthead { display:grid; grid-template-columns:minmax(0,1fr) 190px; gap:20px; align-items:center; padding-bottom:17px; border-bottom:1.5px solid var(--inv-v5-red); break-inside:avoid; }
.inv-v5-brand { display:flex; align-items:center; gap:13px; min-width:0; }
.inv-v5-logo { display:block; width:54px; height:54px; object-fit:contain; flex:0 0 54px; }
.invoice-v5 .inv-v5-title { font-size:24px; line-height:1.12; letter-spacing:-.02em; font-weight:700; margin-bottom:8px; }
.inv-v5-reference { font-size:15px; line-height:1.4; overflow-wrap:anywhere; }
.inv-v5-reference span { margin-left:8px; color:var(--inv-v5-ink); white-space:nowrap; }
.inv-v5-contact { text-align:right; overflow-wrap:anywhere; }
.inv-v5-contact-name { font-size:14px; font-weight:700; margin-bottom:4px; }
.inv-v5-contact-phone { font-size:18px; line-height:1.2; font-weight:700; }
.inv-v5-parties { display:grid; grid-template-columns:1fr 1fr; gap:28px; padding:18px 0 16px; break-inside:avoid; }
.inv-v5-label { font-size:11.5px; line-height:1.3; color:var(--inv-v5-muted); margin-bottom:5px; }
.inv-v5-party-name { font-size:15px; font-weight:700; line-height:1.35; margin-bottom:5px; overflow-wrap:anywhere; }
.inv-v5-party-details { font-size:12px; line-height:1.5; color:var(--inv-v5-muted); overflow-wrap:anywhere; }
.inv-v5-party-ids { display:flex; flex-wrap:wrap; column-gap:12px; row-gap:1px; }
.inv-v5-party-ids span { white-space:nowrap; font-variant-numeric:tabular-nums; }
.inv-v5-party-ids .inv-v5-recipient-inn { font-size:13px; font-weight:600; color:var(--inv-v5-ink); }
.inv-v5-bank { display:grid; grid-template-columns:minmax(0,1.25fr) minmax(0,1fr); column-gap:22px; row-gap:6px; padding:11px 12px; background:var(--inv-v5-light); border-top:.75px solid var(--inv-v5-rule); border-bottom:.75px solid var(--inv-v5-rule); break-inside:avoid; }
.inv-v5-bank-field { display:grid; grid-template-columns:104px minmax(0,1fr); column-gap:7px; align-items:baseline; }
.inv-v5-bank-field:nth-child(even) { grid-template-columns:77px minmax(0,1fr); }
.inv-v5-bank-field .inv-v5-label { margin:0; }
.inv-v5-bank-value { font-size:13px; line-height:1.45; overflow-wrap:anywhere; }
.inv-v5-bank-number { font-size:13.5px; line-height:1.45; white-space:nowrap; font-variant-numeric:tabular-nums; }
.inv-v5-bank-account { font-weight:700; }
.inv-v5-order-link { display:flex; flex-wrap:wrap; justify-content:space-between; column-gap:20px; row-gap:3px; padding:12px 0 17px; font-size:12px; line-height:1.4; color:var(--inv-v5-muted); break-inside:avoid; }
.inv-v5-order-link > span { min-width:0; max-width:100%; overflow-wrap:anywhere; }
.inv-v5-order-link .inv-v5-car { color:var(--inv-v5-ink); }
.inv-v5-items { width:100%; table-layout:fixed; border-collapse:collapse; font-size:14.5px; line-height:1.4; }
.inv-v5-items thead { display:table-header-group; }
.inv-v5-items th { padding:7px 7px; font-size:11.5px; font-weight:700; line-height:1.35; text-align:left; background:var(--inv-v5-light); border-top:.75px solid var(--inv-v5-rule); border-bottom:.75px solid var(--inv-v5-rule); }
.inv-v5-items td { padding:8px 7px; border-bottom:.75px solid var(--inv-v5-rule); vertical-align:top; }
.inv-v5-items tr { break-inside:avoid; page-break-inside:avoid; }
.inv-v5-items .inv-v5-description { overflow-wrap:anywhere; }
.inv-v5-items .inv-v5-number { text-align:center; color:var(--inv-v5-muted); font-size:12px; }
.inv-v5-items .inv-v5-quantity { text-align:center; font-size:13.5px; font-variant-numeric:tabular-nums; }
.inv-v5-items th.inv-v5-quantity { font-size:11.5px; }
.inv-v5-quantity-pair { display:grid; grid-template-columns:minmax(0,1fr) 45px; column-gap:5px; align-items:baseline; width:100%; }
.inv-v5-quantity-value { text-align:right; white-space:nowrap; }
.inv-v5-quantity-unit { text-align:left; font-size:12px; line-height:1.4; color:var(--inv-v5-muted); overflow-wrap:anywhere; }
.inv-v5-items .inv-v5-money { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
.inv-v5-items .inv-v5-line-total { font-weight:600; }
.inv-v5-items .inv-v5-group { break-after:avoid; page-break-after:avoid; }
.inv-v5-items .inv-v5-group td { border:0; padding:8px 7px 0; font-size:11.5px; font-weight:700; color:var(--inv-v5-muted); }
.inv-v5-items .inv-v5-group.inv-v5-section-break td { padding-top:calc(8px + 2.5mm); }
.inv-v5-rounding-note { display:block; font-weight:400; font-size:10.5px; line-height:1.3; color:var(--inv-v5-muted); margin-top:3px; white-space:normal; }
.inv-v5-final { break-inside:avoid; page-break-inside:avoid; }
.inv-v5-settlement { display:grid; grid-template-columns:minmax(0,1fr) var(--inv-v5-settlement-width,310px); column-gap:26px; margin-top:17px; align-items:start; }
.inv-v5-purpose { font-size:12px; line-height:1.5; overflow-wrap:anywhere; padding-top:4px; }
.inv-v5-purpose .inv-v5-label { margin-bottom:6px; }
.inv-v5-totals { width:100%; border-collapse:collapse; font-size:13px; line-height:1.4; font-variant-numeric:tabular-nums; }
.inv-v5-totals td { padding:6px 10px; vertical-align:middle; }
.inv-v5-totals td:first-child { padding-left:0; }
.inv-v5-totals td:last-child { text-align:right; white-space:nowrap; padding-right:0; }
.inv-v5-totals .inv-v5-tax td { font-size:11px; color:var(--inv-v5-muted); padding-top:2px; padding-bottom:6px; }
.inv-v5-totals .inv-v5-tax-exempt td { text-align:left; }
.inv-v5-totals .inv-v5-due td { padding:11px 10px; border-top:1.5px solid var(--inv-v5-red); background:var(--inv-v5-light); }
.inv-v5-totals .inv-v5-due td:first-child { font-size:15px; font-weight:700; }
.inv-v5-totals .inv-v5-due td:last-child { font-size:28px; font-weight:700; line-height:1.12; }
.inv-v5-words { margin-top:11px; padding-bottom:15px; border-bottom:.75px solid var(--inv-v5-rule); font-size:12px; line-height:1.45; color:var(--inv-v5-muted); }
.inv-v5-words-label { margin-right:5px; }
.inv-v5-closing { display:grid; grid-template-columns:minmax(0,1fr) 40mm; column-gap:16mm; align-items:center; margin-top:9px; break-inside:avoid; page-break-inside:avoid; }
.inv-v5-signature { max-width:126mm; transform:translateY(-4.5mm); }
.inv-v5-signature-row { display:grid; grid-template-columns:fit-content(55mm) 26mm minmax(0,1fr); column-gap:12px; align-items:baseline; font-size:13px; line-height:1.4; }
.inv-v5-signature-role { font-weight:700; overflow-wrap:anywhere; }
.inv-v5-signature-rule { display:block; height:15px; border-bottom:.75px solid var(--inv-v5-muted); }
.inv-v5-signature-name { overflow-wrap:anywhere; }
.inv-v5-signature-name-empty { height:15px; border-bottom:.75px solid var(--inv-v5-muted); }
.inv-v5-stamp { width:40mm; height:40mm; display:flex; align-items:center; justify-content:center; color:#747c84; font-size:10.5px; line-height:1.35; text-align:center; }
@page invoice-v5 { size:A4 portrait; margin:9mm 9mm 12mm;
  @top-left { font-family:Arial; font-size:8pt; color:#626971; padding-left:5mm; padding-bottom:2mm; vertical-align:bottom; }
  @bottom-left { content:none; }
  @bottom-right { content:counter(page) ' / ' counter(pages); font-family:Arial; font-size:7.5pt; color:#626971; padding-right:5mm; vertical-align:top; }
}
@page invoice-v5:first { @top-left { content:none; } }
@media print {
  .document-page.invoice-v5 { page:invoice-v5; width:auto; min-height:0; margin:0; padding:5mm 5mm 1mm; border:0; box-shadow:none; }
}
""".strip()

INVOICE_V5_TEMPLATE = """
<div class="document-page invoice-v5">
<style>@page invoice-v5 { @top-left { content:{{{invoice_v5.running_reference_css}}}; } } @page invoice-v5:first { @top-left { content:none; } }</style>

<header class="inv-v5-masthead">
  <div class="inv-v5-brand">
    {{#service.brand_logo_data_uri}}<img class="inv-v5-logo" src="{{service.brand_logo_data_uri}}" alt="AutoStop">{{/service.brand_logo_data_uri}}{{^service.brand_logo_data_uri}}<strong class="inv-v5-logo-fallback">AutoStop</strong>{{/service.brand_logo_data_uri}}
    <div><h1 class="inv-v5-title">Счёт на оплату</h1><p class="inv-v5-reference"><strong>№ {{repair_order.number_display}}</strong><span>от {{dates.document_date_only_display}}</span></p></div>
  </div>
  <div class="inv-v5-contact"><div class="inv-v5-contact-name">{{service.company_name}}</div><div class="inv-v5-contact-phone">{{service.reception_phone_display}}</div></div>
</header>

<section class="inv-v5-parties" aria-label="Стороны счёта">
  <div>
    <div class="inv-v5-label">Получатель платежа</div>
    <div class="inv-v5-party-name">{{service.legal_name}}</div>
    <div class="inv-v5-party-details"><div class="inv-v5-party-ids"><span class="inv-v5-recipient-inn">ИНН {{service.inn}}</span></div><div>{{service.address}}</div></div>
  </div>
  <div>
    <div class="inv-v5-label">Покупатель</div>
    <div class="inv-v5-party-name">{{client.invoice_name_display}}</div>
    <div class="inv-v5-party-details">{{#client.has_requisites}}<div class="inv-v5-party-ids"><span>ИНН {{client.inn}}</span>{{#invoice_v5.has_client_kpp}}<span>КПП {{client.kpp}}</span>{{/invoice_v5.has_client_kpp}}</div><div>{{client.legal_address}}</div>{{/client.has_requisites}}{{^client.has_requisites}}<div>{{client.phone_display}}</div>{{/client.has_requisites}}</div>
  </div>
</section>

<section class="inv-v5-bank" aria-label="Банковские реквизиты получателя">
  <div class="inv-v5-bank-field"><span class="inv-v5-label">Банк получателя</span><span class="inv-v5-bank-value">{{service.bank_name}}</span></div>
  <div class="inv-v5-bank-field"><span class="inv-v5-label">БИК</span><span class="inv-v5-bank-number">{{service.bik}}</span></div>
  <div class="inv-v5-bank-field"><span class="inv-v5-label">Расчётный счёт</span><span class="inv-v5-bank-number inv-v5-bank-account">{{service.settlement_account}}</span></div>
  <div class="inv-v5-bank-field"><span class="inv-v5-label">Корр. счёт</span><span class="inv-v5-bank-number">{{service.correspondent_account}}</span></div>
</section>

<div class="inv-v5-order-link"><span>К заказ-наряду № {{repair_order.number_display}}</span>{{#invoice_v5.vehicle_summary_display}}<span class="inv-v5-car">{{invoice_v5.vehicle_summary_display}}</span>{{/invoice_v5.vehicle_summary_display}}</div>

<table class="inv-v5-items" aria-label="Работы и материалы">
<colgroup><col style="width:4%"><col style="width:{{invoice_v5.column_b_width}}%"><col style="width:{{invoice_v5.column_c_width}}%"><col style="width:{{invoice_v5.column_d_width}}%"><col style="width:{{invoice_v5.column_e_width}}%"></colgroup>
<thead><tr><th class="inv-v5-number">№</th><th>Наименование</th><th class="inv-v5-quantity">Количество</th><th class="inv-v5-money">Цена, ₽</th><th class="inv-v5-money">Сумма, ₽</th></tr></thead>
<tbody>
{{#invoice.line_items}}{{#v5_group_heading}}<tr class="inv-v5-group{{#v5_section_break}} inv-v5-section-break{{/v5_section_break}}"><td></td><td colspan="4">{{v5_group_heading}}</td></tr>{{/v5_group_heading}}<tr><td class="inv-v5-number">{{index}}</td><td class="inv-v5-description">{{name}}</td><td class="inv-v5-quantity"><span class="inv-v5-quantity-pair"><span class="inv-v5-quantity-value">{{quantity_display}}</span><span class="inv-v5-quantity-unit">{{unit_display}}</span></span></td><td class="inv-v5-money">{{price_display}}</td><td class="inv-v5-money inv-v5-line-total">{{total_display}}{{#has_rounding_adjustment}}<small class="inv-v5-rounding-note">Округление {{rounding_adjustment_display}} руб.</small>{{/has_rounding_adjustment}}</td></tr>{{/invoice.line_items}}
{{^invoice.line_items}}<tr><td colspan="5">Работы и материалы не указаны.</td></tr>{{/invoice.line_items}}
</tbody>
</table>

<div class="inv-v5-final">
  <section class="inv-v5-settlement" style="--inv-v5-settlement-width:{{invoice_v5.settlement_width}}px">
    <div class="inv-v5-purpose"><div class="inv-v5-label">Назначение платежа</div><p>{{service.payment_purpose}}</p></div>
    <table class="inv-v5-totals" aria-label="Расчёт к оплате">
      <tr><td>Сумма счёта</td><td>{{invoice.subtotal_display}} ₽</td></tr>
      {{#invoice.has_prepayment}}<tr class="inv-v5-prepayment"><td>Учтённая предоплата</td><td>- {{invoice.prepayment_display}} ₽</td></tr>{{/invoice.has_prepayment}}
      {{#invoice.has_settlement_rounding_adjustment}}<tr><td>Округление зачёта предоплаты</td><td>{{invoice.settlement_rounding_adjustment_display}} ₽</td></tr>{{/invoice.has_settlement_rounding_adjustment}}
      {{#invoice.has_vat}}<tr class="inv-v5-tax"><td>В том числе {{invoice.tax_label}}</td><td>{{invoice.vat_display}} ₽</td></tr>{{/invoice.has_vat}}
      {{^invoice.has_vat}}<tr class="inv-v5-tax inv-v5-tax-exempt"><td colspan="2">{{invoice.tax_label}}</td></tr>{{/invoice.has_vat}}
      <tr class="inv-v5-due"><td>К оплате</td><td>{{invoice.amount_due_display}} ₽</td></tr>
    </table>
  </section>
  <div class="inv-v5-words"><span class="inv-v5-words-label">К оплате прописью:</span>{{invoice.amount_due_words_display}}</div>
  <section class="inv-v5-closing" aria-label="Подпись поставщика">
    <div class="inv-v5-signature"><div class="inv-v5-signature-row"><span class="inv-v5-signature-role">{{invoice_v5.signer_position_display}}</span><span class="inv-v5-signature-rule" aria-label="Подпись"></span>{{#invoice_v5.has_signer_name}}<span class="inv-v5-signature-name">{{service.signer_name}}</span>{{/invoice_v5.has_signer_name}}{{^invoice_v5.has_signer_name}}<span class="inv-v5-signature-name-empty" aria-label="Ф.И.О."></span>{{/invoice_v5.has_signer_name}}</div></div>
    <div class="inv-v5-stamp">М.П.<br>(при наличии)</div>
  </section>
</div>

</div>
""".strip()
