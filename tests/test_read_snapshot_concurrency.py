from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import base64
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import nullcontext
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage import json_store


class ReadSnapshotConcurrencyTests(CardServiceCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="snapshot-concurrency-appdata-")
        self.addCleanup(appdata.cleanup)
        env = patch.dict(os.environ, {"APPDATA": appdata.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.card_id = self.service.create_card({"title": "BEFORE"})["card"]["id"]
        self.service.update_card({"card_id": self.card_id, "repair_order": {"client": "SYNTHETIC"}})

    def assert_read_ignores_service_lock(self, read, *, hold_store=False):
        finished = threading.Event()
        results, errors = [], []

        def run():
            try:
                results.append(read())
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        with self.service._lock, self.store._lock if hold_store else nullcontext():
            worker = threading.Thread(target=run)
            worker.start()
            independent = finished.wait(2)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(independent, "Independent snapshot waited for the writer service lock")
        return results[0]

    def test_single_card_read_does_not_queue_behind_an_unrelated_service_operation(self):
        result = self.assert_read_ignores_service_lock(
            lambda: self.service.get_card({"card_id": self.card_id})
        )
        self.assertEqual(result["card"]["title"], "BEFORE")

    def test_repair_order_list_does_not_queue_behind_an_unrelated_service_operation(self):
        result = self.assert_read_ignores_service_lock(
            lambda: self.service.list_repair_orders({"compact": True})
        )
        self.assertEqual(result["repair_orders"][0]["card_id"], self.card_id)

    def test_card_search_does_not_queue_behind_an_unrelated_service_operation(self):
        result = self.assert_read_ignores_service_lock(
            lambda: self.service.search_cards({"query": "BEFORE"})
        )
        self.assertEqual([row["id"] for row in result["cards"]], [self.card_id])

    def test_inventory_reads_do_not_queue_behind_an_unrelated_service_operation(self):
        if json_store._SNAPSHOT_REQUIRES_FILE_LOCK:
            self.skipTest("POSIX inventory snapshot")
        created = self.service.save_inventory_item(
            {"name": "Synthetic warehouse snapshot", "quantity": "1.25", "unit": "л"}
        )
        item = created["item"]
        listed = self.assert_read_ignores_service_lock(
            lambda: self.service.list_inventory_items({"query": "warehouse snapshot"})
        )
        detail = self.assert_read_ignores_service_lock(
            lambda: self.service.get_inventory_item({"item_id": item["id"]}), hold_store=True
        )
        movements = self.assert_read_ignores_service_lock(
            lambda: self.service.list_inventory_movements({"item_id": item["id"]}),
            hold_store=True,
        )
        self.assertEqual(listed["items"], [item])
        self.assertEqual(detail["item"], item)
        self.assertEqual(detail["movements"], [created["movement"]])
        self.assertEqual(movements["movements"], detail["movements"])

    def test_cashbox_details_do_not_queue_behind_an_unrelated_service_operation(self):
        from minimal_kanban.storage.json_store import _SNAPSHOT_REQUIRES_FILE_LOCK

        if _SNAPSHOT_REQUIRES_FILE_LOCK:
            self.skipTest("POSIX committed file signature")
        box_id = self.service.create_cashbox({"name": "Synthetic snapshot cashbox"})["cashbox"][
            "id"
        ]
        payload = {"cashbox_id": box_id, "compact": True}
        self.service.create_cash_transaction(
            {"cashbox_id": box_id, "direction": "income", "amount_minor": 750}
        )
        with self.store._lock:
            self.store._invalidate_read_cache()
            self.service._finance_read_core._cashbox_details = None
        first = self.assert_read_ignores_service_lock(
            lambda: self.service.get_cashbox(payload), hold_store=True
        )
        second = self.assert_read_ignores_service_lock(
            lambda: self.service.get_cashbox(payload), hold_store=True
        )
        self.assertEqual(first["cashbox"]["statistics"]["balance_minor"], 750)
        self.assertEqual(second, first)

    def test_an_older_card_search_remains_consistent_across_a_card_edit(self):
        index = self.service._snapshot_service._card_search_index
        match_fields = index._match_fields
        matching, release = threading.Event(), threading.Event()

        def paused(fields, prepared):
            if threading.current_thread().name.startswith("old-card-search"):
                matching.set()
                self.assertTrue(release.wait(5))
            return match_fields(fields, prepared)

        with (
            patch.object(index, "_match_fields", side_effect=paused),
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-card-search") as reader,
            ThreadPoolExecutor(max_workers=1) as updater,
        ):
            old = reader.submit(self.service.search_cards, {"query": "BEFORE"})
            try:
                self.assertTrue(matching.wait(2))
                updater.submit(
                    self.service.update_card, {"card_id": self.card_id, "title": "AFTER"}
                ).result(2)
                fresh = self.service.search_cards({"query": "AFTER"})
            finally:
                release.set()
            self.assertEqual(old.result(2)["cards"][0]["title"], "BEFORE")
        self.assertEqual(fresh["cards"][0]["title"], "AFTER")
        self.assertEqual(self.service.search_cards({"query": "BEFORE"})["cards"], [])

    def test_print_workspace_uses_verified_card_while_service_is_busy(self):
        original = self.state_file.read_bytes()
        with patch("minimal_kanban.printing.service.list_printers", return_value=[]):
            result = self.assert_read_ignores_service_lock(
                lambda: self.service.get_repair_order_print_workspace({"card_id": self.card_id})
            )
        self.assertEqual(result["card_id"], self.card_id)
        self.assertIn("BEFORE", result["heading"])
        self.assertTrue(result["meta"]["has_repair_order_data"])
        self.assertEqual(self.state_file.read_bytes(), original)

    def test_readonly_order_and_text_do_not_queue_behind_service_lock(self):
        original = self.state_file.read_bytes()
        views = (
            (self.service.get_repair_order, {"create_if_missing": False}),
            (self.service.get_repair_order_text, {"read_only": True}),
        )
        with patch.object(
            self.store, "_read_single_card_snapshot", wraps=self.store._read_single_card_snapshot
        ) as snapshot:
            for read, options in views:
                with self.subTest(route=read.__name__):
                    result = self.assert_read_ignores_service_lock(
                        lambda: read({"card_id": self.card_id, **options}),
                        hold_store=not json_store._SNAPSHOT_REQUIRES_FILE_LOCK,
                    )
                    self.assertEqual(result["card_id"], self.card_id)
                    self.assertEqual(result["repair_order"]["client"], "SYNTHETIC")
                    self.assertEqual(self.state_file.read_bytes(), original)
        self.assertEqual(snapshot.call_count, 2)

    def test_order_read_retries_after_concurrent_attachment_removal(self):
        attachment = self.service.add_card_attachment(
            {
                "card_id": self.card_id,
                "file_name": "fixture.txt",
                "content_base64": base64.b64encode(b"fixture").decode("ascii"),
            }
        )["attachment"]
        probing, release = threading.Event(), threading.Event()
        exists_on_disk = self.service._attachment_exists_on_disk

        def paused_probe(*args):
            if not probing.is_set():
                probing.set()
                if not release.wait(6):
                    raise TimeoutError("Synthetic attachment check was not released")
            return exists_on_disk(*args)

        with (
            patch.object(self.service, "_attachment_exists_on_disk", side_effect=paused_probe),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            reader = pool.submit(
                self.service.get_repair_order,
                {"card_id": self.card_id, "create_if_missing": False},
            )
            try:
                self.assertTrue(probing.wait(2))
                writer = pool.submit(
                    self.service.remove_card_attachment,
                    {"card_id": self.card_id, "attachment_id": attachment["id"]},
                )
                writer.result(timeout=4)
            finally:
                release.set()
            self.assertEqual(reader.result(timeout=5)["card"]["attachments"], [])

    def assert_parse_allows_commit(self, snapshot, *, requires_file_lock=False):
        parsing, release, committed = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        normalize = self.store._normalize_cards

        def paused_normalize(*args, **kwargs):
            if threading.current_thread().name == "independent-snapshot":
                parsing.set()
                if not release.wait(5):
                    raise TimeoutError("Synthetic snapshot was not released")
            return normalize(*args, **kwargs)

        def read():
            try:
                results.append(snapshot())
            except Exception as exc:
                errors.append(exc)

        def write():
            try:
                self.service.update_card({"card_id": self.card_id, "title": "AFTER"})
                committed.set()
            except Exception as exc:
                errors.append(exc)

        with (
            patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", requires_file_lock),
            patch.object(self.store, "_normalize_cards", side_effect=paused_normalize),
        ):
            reader = threading.Thread(target=read, name="independent-snapshot")
            writer = threading.Thread(target=write)
            reader.start()
            try:
                self.assertTrue(parsing.wait(2))
                writer.start()
                independent = committed.wait(2)
            finally:
                release.set()
                reader.join(5)
                if writer.ident is not None:
                    writer.join(5)
        self.assertFalse(reader.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(independent, "Read-only parsing blocked a concurrent commit")
        return results[0]

    def test_cold_repair_order_snapshot_does_not_block_commit_or_cache_stale_version(self):
        old = self.assert_parse_allows_commit(self.store.read_repair_order_cards_snapshot)
        self.assertEqual(old[0].title, "BEFORE")
        current = self.store.read_repair_order_cards_snapshot()
        self.assertEqual(current[0].title, "AFTER")

    def test_locked_repair_order_snapshot_releases_writer_before_normalization(self):
        old = self.assert_parse_allows_commit(
            self.store.read_repair_order_cards_snapshot, requires_file_lock=True
        )
        self.assertEqual(old[0].title, "BEFORE")
        with patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", True):
            current = self.store.read_repair_order_cards_snapshot()
        self.assertEqual(current[0].title, "AFTER")

    def test_projector_does_not_block_writer_or_next_snapshot(self):
        self.service.list_repair_orders({"status": "all"})
        projecting, release, finished = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        serialize = self.service._serialize_repair_order_list_item

        def paused_serialize(*args, **kwargs):
            if threading.current_thread().name == "paused-order-projector":
                projecting.set()
                if not release.wait(5):
                    raise TimeoutError("Synthetic projector was not released")
            return serialize(*args, **kwargs)

        def read():
            try:
                results.append(self.service.list_repair_orders({"status": "all"}))
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        with patch.object(
            self.service, "_serialize_repair_order_list_item", side_effect=paused_serialize
        ):
            worker = threading.Thread(target=read, name="paused-order-projector")
            worker.start()
            try:
                self.assertTrue(projecting.wait(2))
                self.service.update_card({"card_id": self.card_id, "title": "AFTER"})
                current = self.service.list_repair_orders({"status": "all"})
                self.assertIn("AFTER", current["repair_orders"][0]["heading"])
            finally:
                release.set()
                worker.join(5)
        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])
        self.assertIn("BEFORE", results[0]["repair_orders"][0]["heading"])

    def test_cold_context_snapshot_does_not_block_commit_or_cache_stale_version(self):
        def snapshot():
            return self.store._read_context_bundle_snapshot(domains=("events",))

        old = self.assert_parse_allows_commit(snapshot)
        self.assertEqual(old["cards"][0].title, "BEFORE")
        self.assertEqual(snapshot()["cards"][0].title, "AFTER")

    def test_warm_client_search_uses_the_persisted_file_while_writer_prepares(self):
        client = self.service.create_client({"display_name": "SYNTHETIC BEFORE"})["client"]
        started, release = threading.Event(), threading.Event()
        searches = (self.service.search_clients, self.service.search_clients_readonly)

        def write():
            with self.store._lock:
                started.set()
                if not release.wait(5):
                    raise TimeoutError("Synthetic writer was not released")
                self.service.update_client(
                    {"client_id": client["id"], "display_name": "SYNTHETIC AFTER"}
                )

        with (
            patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", False),
            ThreadPoolExecutor(max_workers=3) as pool,
        ):
            query = {"query": "SYNTHETIC BEFORE"}
            before = [search(query) for search in searches]
            writer = pool.submit(write)
            try:
                self.assertTrue(started.wait(2))
                readers = [pool.submit(search, query) for search in searches]
                _, waiting = wait(readers, timeout=2)
            finally:
                release.set()
            writer.result(timeout=5)
            self.assertFalse(waiting, "Warm searches waited for the store writer")
            self.assertEqual([reader.result(timeout=5) for reader in readers], before)
            for search in searches:
                self.assertEqual(search(query)["clients"], [])
                current = search({"query": "SYNTHETIC AFTER"})
                self.assertEqual(current["clients"][0]["id"], client["id"])

    def test_warm_reads_do_not_wait_for_unrelated_cold_snapshot(self):
        self.service.create_client({"display_name": "SYNTHETIC CUSTOMER"})

        def clients():
            return self.service.search_clients({"query": "SYNTHETIC CUSTOMER"})

        def orders():
            return self.service.list_repair_orders({"status": "all", "compact": True})

        normalize = self.store._normalize_cards
        with patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", False):
            for warm, cold in ((clients, orders), (orders, clients)):
                with self.subTest(warm=warm.__name__):
                    self.service.update_card({"card_id": self.card_id, "title": warm.__name__})
                    expected = warm()
                    parsing, release = threading.Event(), threading.Event()

                    def pause(*args, **kwargs):
                        if threading.current_thread().name == "cold-snapshot_0":
                            parsing.set()
                            if not release.wait(5):
                                raise TimeoutError("Cold snapshot was not released")
                        return normalize(*args, **kwargs)

                    with (
                        patch.object(self.store, "_normalize_cards", side_effect=pause),
                        ThreadPoolExecutor(1, thread_name_prefix="cold-snapshot") as cold_worker,
                        ThreadPoolExecutor(1) as warm_worker,
                    ):
                        building = cold_worker.submit(cold)
                        try:
                            self.assertTrue(parsing.wait(2))
                            self.assertEqual(warm_worker.submit(warm).result(timeout=2), expected)
                        finally:
                            release.set()
                        self.assertTrue(building.result(timeout=5))

    def test_context_load_does_not_hold_service_lock_while_parsing(self):
        def read():
            return self.service.get_card_context(
                {"card_id": self.card_id, "include_repair_order_text": False}
            )

        old = self.assert_parse_allows_commit(read)
        self.assertEqual(old["card"]["title"], "BEFORE")
        self.assertEqual(read()["card"]["title"], "AFTER")

    def test_saved_card_client_suggestions_do_not_block_card_writes(self):
        client_id = self.service.create_client({"display_name": "SYNTHETIC CUSTOMER"})["client"][
            "id"
        ]

        def read():
            return self.service.suggest_clients_for_card(
                {"card_id": self.card_id, "query": "SYNTHETIC CUSTOMER"}
            )

        old = self.assert_parse_allows_commit(read)
        current = read()
        self.assertEqual(old["card"]["title"], "BEFORE")
        self.assertEqual(current["card"]["title"], "AFTER")
        self.assertEqual([item["id"] for item in old["clients"]], [client_id])
        self.assertEqual([item["id"] for item in current["clients"]], [client_id])

    def test_client_reads_and_readonly_card_context_ignore_service_lock(self):
        client = self.service.create_client({"display_name": "SYNTHETIC CLIENT"})["client"]
        for list_clients in (self.service.list_clients, self.service.list_clients_readonly):
            for include_stats in (False, True):
                with self.subTest(route=list_clients.__name__, include_stats=include_stats):
                    result = self.assert_read_ignores_service_lock(
                        lambda: list_clients({"include_stats": include_stats})
                    )
                    self.assertIn(client["id"], {item["id"] for item in result["clients"]})
        for get_client in (self.service.get_client, self.service.get_client_stats):
            with self.subTest(route=get_client.__name__):
                result = self.assert_read_ignores_service_lock(
                    lambda: get_client({"client_id": client["id"]})
                )
                self.assertEqual(result["client"]["id"], client["id"])
        result = self.assert_read_ignores_service_lock(
            lambda: self.service.get_card_context({"card_id": self.card_id, "read_only": True})
        )
        self.assertEqual(result["card"]["title"], "BEFORE")

    def test_client_search_leaves_cashbox_and_writer_free_throughout_search(self):
        cashbox = self.service.create_cashbox({"name": "SYNTHETIC CASHBOX"})["cashbox"]
        for search, prepare, scope in (
            (self.service.search_clients, "_client_checked_search_cards", {}),
            (self.service.search_clients_readonly, "_client_readonly_related_cards", {}),
            (
                self.service.suggest_clients_for_card,
                "_client_checked_search_cards",
                {"card_id": self.card_id},
            ),
        ):
            for owner, method in (
                (self.store, "_read_context_bundle_snapshot"),
                (self.service, prepare),
                (self.service, "_rank_client_matches"),
            ):
                with self.subTest(route=search.__name__, phase=method):
                    client = self.service.create_client({"display_name": "SYNTHETIC BEFORE"})[
                        "client"
                    ]
                    payload = {"query": "SYNTHETIC", **scope}
                    paused, release = threading.Event(), threading.Event()
                    original = getattr(owner, method)

                    def pause_search(*args, **kwargs):
                        result = original(*args, **kwargs)
                        if not paused.is_set():
                            paused.set()
                            if not release.wait(5):
                                raise TimeoutError("Synthetic client search was not released")
                        return result

                    def cashbox_and_write():
                        self.assertEqual(
                            self.service.list_cashboxes()["cashboxes"][0]["id"], cashbox["id"]
                        )
                        self.service.update_client(
                            {"client_id": client["id"], "display_name": "SYNTHETIC AFTER"}
                        )
                        return search(payload)

                    with (
                        patch.object(owner, method, side_effect=pause_search),
                        ThreadPoolExecutor(max_workers=2) as workers,
                    ):
                        before = workers.submit(search, payload)
                        try:
                            self.assertTrue(paused.wait(2))
                            after = workers.submit(cashbox_and_write).result(timeout=2)
                        finally:
                            release.set()

                    for response, expected in (
                        (before.result(), "SYNTHETIC BEFORE"),
                        (after, "SYNTHETIC AFTER"),
                        (search(payload), "SYNTHETIC AFTER"),
                    ):
                        result = next(
                            item for item in response["clients"] if item["id"] == client["id"]
                        )
                        self.assertEqual(result["name"], expected)
