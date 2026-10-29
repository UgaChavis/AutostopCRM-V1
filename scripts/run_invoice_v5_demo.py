"""Run a disposable localhost CRM with the accepted invoice-v5 example.

Usage: python scripts/run_invoice_v5_demo.py
Operator login: admin / admin. All data and bank details are synthetic.
Use --export-only to generate the native PDF without keeping the server running.
Create output/invoice-v5-demo/stop to stop a background demo gracefully.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import logging
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.api.server import ApiServer  # noqa: E402
from minimal_kanban.operator_auth import OperatorAuthService  # noqa: E402
from minimal_kanban.printing.models import PrintServiceProfile  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


def demo_fixture() -> dict:
    """Use the same fictional identity and amounts as the approved design."""
    fixture = json.loads((ROOT / "tests/fixtures/repair_order_v6.json").read_text(encoding="utf-8"))
    card = copy.deepcopy(fixture["card"])
    card["description"] = "Демонстрационные данные счёта v5. Не для оплаты."
    card["repair_order"].update(
        number="ОБРАЗЕЦ-002",
        client="ООО «Пример Автопарк»",
        payment_method="cashless",
        payments=[{"payment_method": "cashless", "amount": "3000"}],
    )
    card["repair_order"]["materials"][0]["inventory_unit"] = "л"
    profile = {key: "" for key in PrintServiceProfile().to_dict()}
    profile.update(
        company_name="AutoStop (пример)",
        legal_name="ООО «Пример Автосервис»",
        address="г. Красноярск, ул. Примерная, д. 10",
        phone="+7 (000) 000-00-00",
        reception_phone="000-00-00",
        spare_parts_phone="000-00-00",
        website="autostop.example",
        email="service@example.test",
        inn="0000000000",
        kpp="000000000",
        ogrn="0000000000000",
        bank_name="АО «Демонстрационный банк»",
        bik="000000000",
        settlement_account="00000000000000000000",
        correspondent_account="00000000000000000000",
        tax_label="Без НДС",
        payment_purpose=(
            "Оплата за ремонт автомобиля / техническое обслуживание / "
            "запасные части по счёту или заказ-наряду."
        ),
        signer_position="Руководитель",
        signer_name="Пример П. П.",
    )
    client = {
        "client_type": "ooo",
        "display_name": "ООО «Пример Автопарк»",
        "legal_name": "ООО «Пример Автопарк»",
        "short_name": "Пример Автопарк",
        "phone": "+7 (000) 000-00-01",
        "email": "buyer@example.test",
        "inn": "1111111111",
        "kpp": "111111111",
        "ogrn": "1111111111111",
        "checking_account": "11111111111111111111",
        "correspondent_account": "11111111111111111111",
        "bank_name": "АО «Пример Банк»",
        "bik": "111111111",
        "legal_address": "г. Красноярск, ул. Учебная, д. 20",
        "actual_address": "г. Красноярск, ул. Учебная, д. 20",
        "contact_person": "Пример Иван Иванович",
        "contact_position": "Руководитель автопарка",
    }
    return {"card": card, "client": client, "service_profile": profile}


def build_demo_service(data: Path, logger: logging.Logger) -> tuple[JsonStore, CardService, str]:
    fixture = demo_fixture()
    store = JsonStore(state_file=data / "state.json", logger=logger)
    service = CardService(
        store, logger, attachments_dir=data / "attachments", repair_orders_dir=data / "orders"
    )
    service.set_onboarding_seen(True)
    service.save_print_module_settings({"service_profile": fixture["service_profile"]})
    client = service.create_client({"client": fixture["client"], "actor_name": "Демо"})["client"]
    card = service.create_card(
        {
            **{key: fixture["card"][key] for key in ("vehicle", "title", "description")},
            "client_id": client["id"],
            "actor_name": "Демо",
        }
    )["card"]
    service.update_repair_order(
        {
            "card_id": card["id"],
            "repair_order": fixture["card"]["repair_order"],
            "actor_name": "Демо",
        }
    )
    return store, service, card["id"]


def export_demo(service: CardService, card_id: str, output: Path) -> dict:
    request = {
        "card_id": card_id,
        "selected_document_ids": ["invoice"],
        "active_document_id": "invoice",
    }
    preview = service.preview_repair_order_print_documents(request)
    exported = service.export_repair_order_print_pdf(request)
    assert exported["meta"]["money_validation"]["valid"]
    output.mkdir(parents=True, exist_ok=True)
    (output / "invoice-v5-crm.pdf").write_bytes(base64.b64decode(exported["content_base64"]))
    (output / "invoice-v5-crm.html").write_text(
        preview["documents"][0]["pages"][0]["html"], encoding="utf-8"
    )
    (output / "export-meta.json").write_text(
        json.dumps(exported["meta"], ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return {"pdf": str(output / "invoice-v5-crm.pdf"), "money_valid": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--output", type=Path, default=ROOT / "output/invoice-v5-demo")
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    stop_file = output / "stop"
    if stop_file.exists():
        parser.error(f"Remove the previous demo stop marker before starting: {stop_file}")
    logger = logging.getLogger("autostop.invoice-v5-demo")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory(prefix="autostop-invoice-v5-demo-") as directory:
        data = Path(directory)
        store, service, card_id = build_demo_service(data, logger)
        exported = export_demo(service, card_id, output)
        if args.export_only:
            print(json.dumps(exported), flush=True)
            return
        operators = OperatorAuthService(
            store, service, users_file=data / "users.json", logger=logger
        )
        api = ApiServer(
            service,
            logger,
            operator_service=operators,
            host="127.0.0.1",
            start_port=args.port,
            fallback_limit=1,
            bearer_token="invoice-v5-local-demo",
        )
        try:
            api.start()
            details = {
                "url": api.base_url + "/?access_token=invoice-v5-local-demo",
                "card_id": card_id,
                "data_dir": str(data),
                "operator": "admin",
                "stop_file": str(stop_file),
                **exported,
            }
            (output / "server.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
            print(json.dumps(details), flush=True)
            while not stop_file.exists():
                threading.Event().wait(1)
        except KeyboardInterrupt:
            pass
        finally:
            api.stop()


if __name__ == "__main__":
    main()
