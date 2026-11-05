"""Read-only snapshot fast-path admission and legacy fallback."""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.storage import readonly_context_snapshot as snapshots  # noqa: E402


def supported_runtime(**overrides):
    values = {
        "implementation": SimpleNamespace(name="cpython"),
        "version_info": (3, 12),
        "getrecursionlimit": sys.getrecursionlimit,
        "gettrace": sys.gettrace,
        "getprofile": sys.getprofile,
        "_getframe": sys._getframe,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def nested_containers(depth):
    value = {}
    for _ in range(depth):
        value = [value]
    return value


class StringSubclass(str):
    pass


class IntegerSubclass(int):
    pass


class FakeStore:
    def __init__(self):
        self.calls = []

    def _normalize_columns(self, state):
        return [], False

    def _normalize_cards(self, state, columns, *, detach_order_history=False):
        self.calls.append((state["cards"], detach_order_history))
        return [], False


class SnapshotShallowJsonGuardTests(unittest.TestCase):
    def setUp(self):
        # Keep boundary tests independent of the Python version used by the test
        # runner while retaining the real recursion-limit and frame behavior.
        fake = supported_runtime(gettrace=lambda: None, getprofile=lambda: None)
        patcher = patch.object(snapshots, "sys", fake)
        self.runtime = patcher.start()
        self.addCleanup(patcher.stop)

    def test_accepts_detached_shallow_builtin_json_and_depth_32(self):
        self.assertTrue(
            snapshots._safe_to_detach_shallow_json(
                {
                    "client": [None, True, False, 0, 12, 1.5, "text", {"empty": []}],
                }
            )
        )
        self.assertTrue(snapshots._safe_to_detach_shallow_json(nested_containers(32)))
        self.assertFalse(snapshots._safe_to_detach_shallow_json(nested_containers(33)))
        # The bound applies to containers; an immutable scalar may be one level deeper.
        at_limit = nested_containers(32)
        cursor = at_limit
        for _ in range(32):
            cursor = cursor[0]
        cursor["leaf"] = "value"
        self.assertTrue(snapshots._safe_to_detach_shallow_json(at_limit))

    def test_shared_containers_and_cycles_require_deepcopy(self):
        shared_list = []
        shared_dict = {"value": 1}
        cycle = []
        cycle.append(cycle)
        indirect = {}
        indirect["loop"] = [indirect]
        for value in ([shared_list, shared_list], [shared_dict, shared_dict], cycle, indirect):
            with self.subTest(kind=type(value).__name__):
                self.assertFalse(snapshots._safe_to_detach_shallow_json(value))
        self.assertTrue(snapshots._safe_to_detach_shallow_json(["same", "same", 1, 1]))

    def test_non_native_values_and_non_string_keys_require_deepcopy(self):
        unusual = (
            (1, 2),
            {1, 2},
            frozenset({1}),
            b"bytes",
            Decimal("1"),
            object(),
            StringSubclass("text"),
            IntegerSubclass(1),
        )
        for value in unusual:
            with self.subTest(value_type=type(value).__name__):
                self.assertFalse(snapshots._safe_to_detach_shallow_json({"value": value}))
        for key in (1, None, True, StringSubclass("key")):
            with self.subTest(key_type=type(key).__name__):
                self.assertFalse(snapshots._safe_to_detach_shallow_json({key: "value"}))

    def test_runtime_and_recursion_limit_require_fallback(self):
        self.runtime.implementation = SimpleNamespace(name="pypy")
        self.assertFalse(snapshots._safe_to_detach_shallow_json([]))
        self.runtime.implementation = SimpleNamespace(name="cpython")
        self.runtime.version_info = (3, 11)
        self.assertFalse(snapshots._safe_to_detach_shallow_json([]))
        self.runtime.version_info = (3, 13)
        self.assertFalse(snapshots._safe_to_detach_shallow_json([]))
        self.runtime.version_info = (3, 12)
        original_limit = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(300)
            self.assertFalse(snapshots._safe_to_detach_shallow_json([]))
        finally:
            sys.setrecursionlimit(original_limit)

    def test_actual_trace_and_profile_hooks_require_fallback(self):
        self.runtime.gettrace = sys.gettrace
        self.runtime.getprofile = sys.getprofile
        old_trace, old_profile = sys.gettrace(), sys.getprofile()
        try:
            sys.settrace(lambda frame, event, arg: None)
            self.assertFalse(snapshots._safe_to_detach_shallow_json([]))
            sys.settrace(None)
            sys.setprofile(lambda frame, event, arg: None)
            self.assertFalse(snapshots._safe_to_detach_shallow_json([]))
        finally:
            sys.settrace(old_trace)
            sys.setprofile(old_profile)

    def test_deep_python_call_stack_requires_fallback(self):
        def descend(remaining):
            if remaining:
                return descend(remaining - 1)
            return snapshots._safe_to_detach_shallow_json([])

        self.assertTrue(descend(2))
        self.assertFalse(descend(140))

    def test_client_snapshot_uses_old_copy_when_guard_rejects(self):
        original_search = snapshots._search_clients
        for depth, should_copy in ((2, False), (33, True)):
            with self.subTest(depth=depth):
                clients = [{"id": "synthetic-client", "unused": nested_containers(depth)}]
                state = {"clients": clients, "events": []}
                seen = []

                def search(rows):
                    seen.append(rows)
                    return original_search(rows)

                with patch.object(snapshots, "_search_clients", search):
                    snapshots.build_context_snapshot(None, state, ("clients", "client_search"))
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0], clients)
                self.assertEqual(seen[0] is not clients, should_copy)

    def test_profile_snapshot_uses_old_copy_when_guard_rejects(self):
        for depth, should_copy in ((2, False), (33, True)):
            with self.subTest(depth=depth):
                cards = [{"id": "synthetic-card", "unused": nested_containers(depth)}]
                state = {"cards": cards, "clients": [], "events": []}
                store = FakeStore()
                snapshots.build_context_snapshot(store, state, ("client_profile",))
                self.assertEqual(len(store.calls), 1)
                received, detach = store.calls[0]
                self.assertEqual(received, cards)
                self.assertEqual(received is not cards, should_copy)
                self.assertEqual(detach, not should_copy)


if __name__ == "__main__":
    unittest.main()
