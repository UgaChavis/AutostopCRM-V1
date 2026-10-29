"""Run a disposable, localhost-only CRM with the approved repair-order example.

Usage: python scripts/run_repair_order_v6_demo.py
The operator login is admin / admin. Closing the process removes its temporary data.
"""

from __future__ import annotations

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    fixture = json.loads((ROOT / "tests/fixtures/repair_order_v6.json").read_text(encoding="utf-8"))
    logger = logging.getLogger("autostop.repair-order-v6-demo")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory(prefix="autostop-repair-order-v6-demo-") as directory:
        data = Path(directory)
        store = JsonStore(state_file=data / "state.json", logger=logger)
        service = CardService(
            store, logger, attachments_dir=data / "attachments", repair_orders_dir=data / "orders"
        )
        service.set_onboarding_seen(True)
        profile = {key: "Демонстрационные данные" for key in PrintServiceProfile().to_dict()}
        profile.update(fixture["service_profile"])
        service.save_print_module_settings({"service_profile": profile})
        card = service.create_card(
            {
                **{key: fixture["card"][key] for key in ("vehicle", "title", "description")},
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
            bearer_token="repair-order-v6-local-demo",
        )
        try:
            api.start()
            print(
                json.dumps(
                    {
                        "url": api.base_url + "/?access_token=repair-order-v6-local-demo",
                        "card_id": card["id"],
                        "data_dir": str(data),
                        "operator": "admin",
                    }
                ),
                flush=True,
            )
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            api.stop()


if __name__ == "__main__":
    main()
