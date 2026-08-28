from __future__ import annotations

# ruff: noqa: E402,I001

import base64
import logging
import socket
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.operator_activity import OperatorActivityService
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.operator_permissions import SALARY_BALANCE_RESET_PERMISSION
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.shared_files_service import SharedFilesService
from minimal_kanban.storage.json_store import JsonStore


@dataclass
class TempRuntime:
    temp_dir: tempfile.TemporaryDirectory[str]
    api: ApiServer
    service: CardService
    state_store: JsonStore
    cashbox_id: str
    card_id: str
    extra_column_card_id: str
    employee_id: str
    salary_reset_employee_id: str
    payroll_card_id: str
    payroll_month: str
    salary_override_card_id: str
    client_id: str
    client_card_id: str
    archived_card_id: str
    api_token: str

    @property
    def base_url(self) -> str:
        return self.api.base_url

    @property
    def browser_url(self) -> str:
        return f"{self.base_url}/?access_token={self.api_token}"

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"}

    def authenticated_url(self, path: str) -> str:
        separator = "&" if "?" in path else "?"
        return f"{self.base_url}{path}{separator}access_token={self.api_token}"

    def close(self) -> None:
        self.api.stop()
        self.temp_dir.cleanup()


def _logger() -> logging.Logger:
    logger = logging.getLogger("autostop.browser_smoke")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def _port_has_listener(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def _first_free_port(start_port: int, *, host: str = "127.0.0.1", limit: int = 50) -> int:
    for candidate in range(start_port, start_port + limit):
        if not _port_has_listener(host, candidate):
            return candidate
    raise RuntimeError("Не удалось найти свободный локальный порт для browser smoke.")


def start_temp_runtime(*, start_port: int = 42731) -> TempRuntime:
    temp_dir = tempfile.TemporaryDirectory(prefix="autostop-browser-smoke-")
    base_dir = Path(temp_dir.name)
    logger = _logger()
    start_port = _first_free_port(start_port)
    store = JsonStore(state_file=base_dir / "state.json", logger=logger)
    service = CardService(
        store,
        logger,
        attachments_dir=base_dir / "attachments",
        repair_orders_dir=base_dir / "repair-orders",
    )
    service.set_onboarding_seen(True)
    cashbox = service.create_cashbox({"name": "Наличный", "actor_name": "SMOKE"})["cashbox"]
    service.create_cashbox({"name": "Безналичный", "actor_name": "SMOKE"})
    service.create_cash_transaction(
        {
            "cashbox_id": cashbox["id"],
            "direction": "income",
            "amount": "1000",
            "note": "Smoke opening balance",
            "actor_name": "SMOKE",
        }
    )
    for index in range(260):
        service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income" if index % 3 else "expense",
                "amount": str(10 + index),
                "note": f"Smoke journal batch {index:03d}",
                "actor_name": "SMOKE",
            }
        )
    card = service.create_card(
        {
            "vehicle": "Toyota Smoke",
            "title": "Browser smoke initial",
            "description": "Temporary card created by browser smoke.",
            "actor_name": "SMOKE",
        }
    )["card"]
    extra_column_card = service.create_card(
        {
            "vehicle": "Toyota Extra Column Smoke",
            "title": "Browser smoke extra column",
            "tags": [
                {"label": "ГОТОВ", "color": "green"},
                {"label": "SMOKE ПЕРВАЯ", "color": "green"},
                {"label": "SMOKE ВТОРАЯ", "color": "yellow"},
                {"label": "НАДО ЧТО ТО СДЕЛАТЬ", "color": "red"},
            ],
            "actor_name": "SMOKE",
        }
    )["card"]
    employee = service.save_employee(
        {
            "name": "Smoke Мастер",
            "position": "Механик",
            "salary_mode": "salary_plus_percent",
            "base_salary": "40000",
            "work_percent": "25",
            "actor_name": "SMOKE",
        }
    )["employee"]
    salary_reset_employee = service.save_employee(
        {
            "name": "Smoke Обнуление Баланса",
            "position": "Механик",
            "salary_mode": "none",
            "actor_name": "SMOKE",
        }
    )["employee"]
    service.create_employee_shift_accrual(
        {
            "employee_id": salary_reset_employee["id"],
            "amount_minor": 12345,
            "note": "Smoke синтетическое начисление для обнуления баланса",
            "actor_name": "SMOKE",
        }
    )
    for index in range(1, 16):
        ranking_employee = service.save_employee(
            {
                "name": f"Smoke Сотрудник {index:02d}",
                "position": "Механик",
                "salary_mode": "none",
                "actor_name": "SMOKE",
            }
        )["employee"]
        service.create_employee_shift_accrual(
            {
                "employee_id": ranking_employee["id"],
                "amount": (f"{(16 - index) * 1000}.01" if index == 1 else str((16 - index) * 1000)),
                "note": "Smoke недельный рейтинг",
                "actor_name": "SMOKE",
            }
        )
    payroll_card = service.create_card(
        {
            "vehicle": "Lada Payroll Smoke",
            "title": "Browser smoke payroll order",
            "deadline": {"hours": 2},
            "actor_name": "SMOKE",
        }
    )["card"]
    service.update_card(
        {
            "card_id": payroll_card["id"],
            "repair_order": {
                "number": "901",
                "status": "open",
                "vehicle": "Lada Payroll Smoke",
                "payments": [
                    {
                        "amount": "20000",
                        "paid_at": "18.05.2026 10:00",
                        "payment_method": "cash",
                    }
                ],
                "works": [
                    {
                        "name": "Smoke payroll work",
                        "quantity": "1",
                        "price": "20000",
                        "executor_id": employee["id"],
                        "work_salary_override_enabled": "true",
                        "work_salary_guarantee": "5000",
                        "work_salary_percent_override": "45",
                        "work_salary_note": "Smoke salary override",
                    }
                ],
            },
            "actor_name": "SMOKE",
        }
    )
    closed_payroll = service.set_repair_order_status(
        {"card_id": payroll_card["id"], "status": "closed", "actor_name": "SMOKE"}
    )
    payroll_month = datetime.strptime(
        closed_payroll["repair_order"]["closed_at"], "%d.%m.%Y %H:%M"
    ).strftime("%Y-%m")
    salary_override_card = service.create_card(
        {
            "vehicle": "Lada Salary Override",
            "title": "Browser smoke salary override gear",
            "deadline": {"hours": 2},
            "actor_name": "SMOKE",
        }
    )["card"]
    service.update_card(
        {
            "card_id": salary_override_card["id"],
            "repair_order": {
                "number": "903",
                "status": "open",
                "vehicle": "Lada Salary Override",
                "payments": [
                    {
                        "amount": "20000",
                        "paid_at": "18.05.2026 11:00",
                        "payment_method": "cash",
                    }
                ],
                "works": [
                    {
                        "name": "Smoke override gear work",
                        "quantity": "1",
                        "price": "20000",
                        "executor_id": employee["id"],
                    }
                ],
            },
            "actor_name": "SMOKE",
        }
    )
    client = service.create_client(
        {
            "display_name": "Smoke Клиент",
            "phone": "+7 900 000-00-01",
            "actor_name": "SMOKE",
        }
    )["client"]
    client_card = service.create_card(
        {
            "vehicle": "Nissan Client Smoke",
            "title": "Browser smoke client order",
            "deadline": {"hours": 2},
            "actor_name": "SMOKE",
        }
    )["card"]
    service.link_card_to_client(
        {"card_id": client_card["id"], "client_id": client["id"], "actor_name": "SMOKE"}
    )
    service.update_card(
        {
            "card_id": client_card["id"],
            "repair_order": {
                "number": "902",
                "status": "open",
                "client": "Smoke Клиент",
                "vehicle": "Nissan Client Smoke",
                "works": [{"name": "Smoke client work", "quantity": "1", "price": "2500"}],
            },
            "actor_name": "SMOKE",
        }
    )
    for index in range(18):
        list_card = service.create_card(
            {
                "vehicle": f"Smoke Scroll Vehicle {index:02d}",
                "title": f"Browser smoke repair-order scroll row {index:02d}",
                "deadline": {"hours": 2},
                "actor_name": "SMOKE",
            }
        )["card"]
        service.update_card(
            {
                "card_id": list_card["id"],
                "repair_order": {
                    "number": str(920 + index),
                    "status": "open",
                    "vehicle": f"Smoke Scroll Vehicle {index:02d}",
                    "works": [
                        {
                            "name": f"Smoke scrolling work {index:02d}",
                            "quantity": "1",
                            "price": "1000",
                        }
                    ],
                },
                "actor_name": "SMOKE",
            }
        )
    archived_card = service.create_card(
        {
            "vehicle": "Archive Filter Smoke",
            "title": "Browser smoke archived search target",
            "description": "Archive search regression row.",
            "deadline": {"hours": 2},
            "actor_name": "SMOKE",
        }
    )["card"]
    service.archive_card({"card_id": archived_card["id"], "actor_name": "SMOKE"})
    shared_files_service = SharedFilesService(
        storage_dir=base_dir / "shared-files",
        index_file=base_dir / "shared_files_index.json",
        logger=logger,
    )
    shared_files_service.upload_shared_file(
        {
            "file_name": "Очень длинное имя файла для проверки читаемости smoke report.txt",
            "content_base64": base64.b64encode(b"autostop smoke shared file").decode("ascii"),
            "mime_type": "text/plain",
            "x": 24,
            "y": 24,
            "actor_name": "SMOKE",
            "source": "system",
        }
    )
    operator_service = OperatorAuthService(
        store,
        service,
        users_file=base_dir / "users.json",
        activity_service=OperatorActivityService(
            activity_dir=base_dir / "operator-activity",
            logger=logger,
        ),
        logger=logger,
    )
    admin_session = operator_service.login({"username": "admin", "password": "admin"})["session"]
    operator_service.save_user(
        {
            "_operator_session": admin_session,
            "username": admin_session["username"],
            "permissions": [SALARY_BALANCE_RESET_PERMISSION],
            "source": "smoke",
        }
    )
    operator_service.set_user_employee(
        {
            "_operator_session": admin_session,
            "username": admin_session["username"],
            "employee_id": employee["id"],
            "source": "smoke",
        }
    )
    api_token = "browser-smoke-local-token"
    api = ApiServer(
        service,
        logger,
        operator_service=operator_service,
        host="127.0.0.1",
        start_port=start_port,
        fallback_limit=50,
        bearer_token=api_token,
        shared_files_service=shared_files_service,
    )
    api.start()
    return TempRuntime(
        temp_dir=temp_dir,
        api=api,
        service=service,
        state_store=store,
        cashbox_id=cashbox["id"],
        card_id=card["id"],
        extra_column_card_id=extra_column_card["id"],
        employee_id=employee["id"],
        salary_reset_employee_id=salary_reset_employee["id"],
        payroll_card_id=payroll_card["id"],
        payroll_month=payroll_month,
        salary_override_card_id=salary_override_card["id"],
        client_id=client["id"],
        client_card_id=client_card["id"],
        archived_card_id=archived_card["id"],
        api_token=api_token,
    )
