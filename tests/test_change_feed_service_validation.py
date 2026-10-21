from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, call

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.change_feed_service import (  # noqa: E402
    AUTOMATION_CHANGE_FEED_CONSUMER,
    ChangeFeedService,
)
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.storage.change_feed_store import ChangeFeedStore  # noqa: E402


class ChangeFeedConsumerValidationTests(unittest.TestCase):
    OPERATIONS = {
        "bootstrap": ("bootstrap", (), {}, "crm_change_feed_bootstrap_v1"),
        "read": ("read_page", (), {"cursor": None, "limit": 25}, "crm_change_feed_page_v1"),
        "register": (
            "register_consumer",
            (),
            {"start_at": "latest"},
            "crm_change_feed_registration_v1",
        ),
        "ack": ("acknowledge", ("synthetic-ack",), {}, "crm_change_feed_ack_v1"),
        "summarize": ("summarize_delivery", ("synthetic-ack",), {}, None),
    }

    def test_rejected_consumers_never_reconcile_or_touch_the_backend(self) -> None:
        reserved = AUTOMATION_CHANGE_FEED_CONSUMER
        rejected = (
            ({}, "validation_error", 400),
            ({"consumer_id": reserved}, "change_feed_consumer_reserved", 403),
            ({"consumer_id": f" \t{reserved}\n"}, "change_feed_consumer_reserved", 403),
            (
                {"consumer_id": "another-consumer", "_automation_service": True},
                "change_feed_consumer_scope_invalid",
                403,
            ),
            (
                {"consumer_id": " another-consumer ", "_automation_service": True},
                "change_feed_consumer_scope_invalid",
                403,
            ),
            (
                {"consumer_id": None, "_automation_service": True},
                "change_feed_consumer_scope_invalid",
                403,
            ),
        )
        for operation in self.OPERATIONS:
            for payload, code, status in rejected:
                with self.subTest(operation=operation, payload=payload):
                    backend = Mock()
                    reconcile = Mock(side_effect=OSError("synthetic backend unavailable"))
                    service = ChangeFeedService(backend, reconcile=reconcile)
                    with self.assertRaises(ServiceError) as rejected_request:
                        getattr(service, operation)({"ack": "synthetic-ack", **payload})
                    self.assertEqual(code, rejected_request.exception.code)
                    self.assertEqual(status, rejected_request.exception.status_code)
                    reconcile.assert_not_called()
                    self.assertEqual([], backend.mock_calls)

    def test_permitted_consumers_keep_reconciliation_before_backend_dispatch(self) -> None:
        reserved = AUTOMATION_CHANGE_FEED_CONSUMER
        permitted = (
            {"consumer_id": "owner"},
            {"consumer_id": " owner "},
            {"consumer_id": reserved, "_automation_service": True},
            {"consumer_id": f" \t{reserved}\n", "_automation_service": True},
        )
        for operation, (
            backend_name,
            arguments,
            keywords,
            response_format,
        ) in self.OPERATIONS.items():
            for payload in permitted:
                with self.subTest(operation=operation, payload=payload):
                    consumer = payload["consumer_id"].strip()
                    backend, reconcile, observed = Mock(), Mock(), Mock()
                    getattr(backend, backend_name).return_value = {"consumer_id": consumer}
                    observed.attach_mock(reconcile, "reconcile")
                    observed.attach_mock(backend, "backend")
                    service = ChangeFeedService(backend, reconcile=reconcile)
                    result = getattr(service, operation)({"ack": "synthetic-ack", **payload})
                    self.assertEqual(
                        [
                            call.reconcile(),
                            getattr(call.backend, backend_name)(consumer, *arguments, **keywords),
                        ],
                        observed.mock_calls,
                    )
                    expected = {"consumer_id": consumer}
                    if response_format is not None:
                        expected["format"] = response_format
                    self.assertEqual(expected, result)

    def test_technical_registration_requires_latest_after_consumer_normalization(self) -> None:
        for consumer in (AUTOMATION_CHANGE_FEED_CONSUMER, f" {AUTOMATION_CHANGE_FEED_CONSUMER} "):
            for start_at in ("beginning", " beginning ", ""):
                with self.subTest(consumer=consumer, start_at=start_at):
                    backend, reconcile = Mock(), Mock()
                    service = ChangeFeedService(backend, reconcile=reconcile)
                    with self.assertRaises(ServiceError) as rejected:
                        service.register(
                            {
                                "consumer_id": consumer,
                                "_automation_service": True,
                                "start_at": start_at,
                            }
                        )
                    self.assertEqual("change_feed_start_at_not_allowed", rejected.exception.code)
                    self.assertEqual(400, rejected.exception.status_code)
                    reconcile.assert_not_called()
                    self.assertEqual([], backend.mock_calls)

    def test_permitted_consumer_still_reports_reconciliation_failure_as_unavailable(self) -> None:
        for operation in self.OPERATIONS:
            with self.subTest(operation=operation):
                backend = Mock()
                reconcile = Mock(side_effect=OSError("synthetic backend unavailable"))
                service = ChangeFeedService(backend, reconcile=reconcile)
                with self.assertRaises(ServiceError) as unavailable:
                    getattr(service, operation)({"consumer_id": "owner", "ack": "synthetic-ack"})
                self.assertEqual("change_feed_unavailable", unavailable.exception.code)
                self.assertEqual(503, unavailable.exception.status_code)
                reconcile.assert_called_once_with()
                self.assertEqual([], backend.mock_calls)

    def test_backend_consumer_type_empty_and_case_sensitive_validation_stays_intact(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            closing(ChangeFeedStore(Path(temporary) / "synthetic.sqlite3")) as store,
        ):
            service = ChangeFeedService(store)
            for consumer in (None, 123, True, [], {}, "", " \t ", "invalid consumer"):
                with self.subTest(consumer=consumer), self.assertRaises(ServiceError) as invalid:
                    service.bootstrap({"consumer_id": consumer})
                self.assertEqual("invalid_consumer", invalid.exception.code)
                self.assertEqual(400, invalid.exception.status_code)
            for consumer in (" Owner ", AUTOMATION_CHANGE_FEED_CONSUMER.upper()):
                with self.subTest(consumer=consumer):
                    result = service.bootstrap({"consumer_id": consumer})
                    self.assertEqual(consumer.strip(), result["consumer_id"])
            for consumer in (
                AUTOMATION_CHANGE_FEED_CONSUMER,
                f" {AUTOMATION_CHANGE_FEED_CONSUMER} ",
            ):
                with self.subTest(consumer=consumer), self.assertRaises(ServiceError) as rejected:
                    service.bootstrap({"consumer_id": consumer})
                self.assertEqual("change_feed_consumer_reserved", rejected.exception.code)
                self.assertEqual(403, rejected.exception.status_code)
            self.assertFalse(
                store.readiness(AUTOMATION_CHANGE_FEED_CONSUMER)["consumer_registered"]
            )


if __name__ == "__main__":
    unittest.main()
