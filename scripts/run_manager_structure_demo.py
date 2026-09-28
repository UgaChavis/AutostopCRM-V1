"""Run a disposable local CRM with the portable manager structure template."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from manager_structure_template import (  # noqa: E402
    DEFAULT_TEMPLATE,
    Client,
    build_reference,
    export,
    restore,
)

from minimal_kanban.api.server import ApiServer  # noqa: E402
from minimal_kanban.operator_activity import OperatorActivityService  # noqa: E402
from minimal_kanban.operator_auth import OperatorAuthService  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


def main() -> int:
    os.environ.setdefault("AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN", "ADMIN")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-reference",
        action="store_true",
        help="Build the sample from the reference by individual CRM operations, then export it",
    )
    parser.add_argument("--port", type=int, default=42991)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "output" / "manager-structure-demo",
        help="Каталог локальной демонстрационной CRM; схема сохраняется между запусками",
    )
    args = parser.parse_args()
    logger = logging.getLogger("manager-structure-demo")
    logger.addHandler(logging.NullHandler())
    context = (
        nullcontext(args.data_dir)
        if args.data_dir
        else tempfile.TemporaryDirectory(prefix="autostop-manager-demo-")
    )
    with context as directory:
        base = Path(directory)
        base.mkdir(parents=True, exist_ok=True)
        store = JsonStore(state_file=base / "state.json", logger=logger)
        service = CardService(
            store,
            logger,
            attachments_dir=base / "attachments",
            repair_orders_dir=base / "repair-orders",
        )
        operator = OperatorAuthService(
            store,
            service,
            users_file=base / "users.json",
            activity_service=OperatorActivityService(
                activity_dir=base / "operator-activity", logger=logger
            ),
            logger=logger,
        )
        api = ApiServer(
            service,
            logger,
            operator_service=operator,
            host="127.0.0.1",
            start_port=args.port,
            fallback_limit=20,
            bearer_token="",
        )
        api.start()
        try:
            session = operator.login({"username": "admin", "password": "admin"})["session"]["token"]
            client = Client(api.base_url, session)
            current = client.read()
            if args.build_reference:
                result = build_reference(client)
                export(client, DEFAULT_TEMPLATE)
            elif current["elements"] or current["relations"]:
                result = current
            else:
                result = restore(client, DEFAULT_TEMPLATE)
            print(f"Локальная демо CRM: {api.base_url}/manager-structure", flush=True)
            print(
                "Войдите в CRM через главную страницу как admin / admin, затем откройте адрес выше.",
                flush=True,
            )
            print(
                f"Схема: {len(result['elements'])} модулей, {len(result['relations'])} связей, версия {result['version']}.",
                flush=True,
            )
            print(f"Данные демонстрации: {base}", flush=True)
            print("Нажмите Ctrl+C для остановки.", flush=True)
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0
        finally:
            api.stop()


if __name__ == "__main__":
    raise SystemExit(main())
