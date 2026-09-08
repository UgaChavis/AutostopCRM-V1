import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.json_safety import (  # noqa: E402
    bounded_dict_list,
    bounded_text_list,
    find_mapping,
    json_safe_api_value,
    json_safe_storage_value,
    json_safe_value,
)


class JsonValuePolicyTests(unittest.TestCase):
    def test_bounded_lists_preserve_selection_coercion_and_shallow_copy(self):
        nested = {"child": 1}
        source = [None, {"nested": nested}, "ignored", {"last": 2}]
        result = bounded_dict_list(source, limit=1)
        self.assertEqual(result, [{"nested": nested}])
        self.assertIsNot(result[0], source[1])
        self.assertIs(result[0]["nested"], nested)
        self.assertEqual(bounded_dict_list(tuple(source), limit=2), [])
        self.assertEqual(bounded_text_list([None, " ", 0, " A ", 2], limit=2), ["A", "2"])
        self.assertEqual(bounded_text_list(" A ", limit=2), ["A"])
        self.assertEqual(bounded_text_list(("A",), limit=2), [])
        # Existing helpers append before checking the bound; retain that contract.
        self.assertEqual(bounded_text_list(["A", "B"], limit=0), ["A"])
        self.assertEqual(bounded_dict_list(source, limit=0), [{"nested": nested}])

    def test_mapping_lookup_preserves_order_depth_and_list_scan_limit(self):
        match = {"id": 4, "nested": {"kept": True}}
        found = find_mapping({"rows": [match, {"id": "4", "later": True}]}, "id", "4")
        self.assertEqual(found, match)
        self.assertIsNot(found, match)
        self.assertIs(found["nested"], match["nested"])
        self.assertIsNone(find_mapping([{}] * 200 + [match], "id", 4))
        self.assertIsNone(find_mapping((match,), "id", 4))
        self.assertEqual(find_mapping(match, "id", 4, depth=7), match)
        self.assertIsNone(find_mapping(match, "id", 4, depth=8))
        circular = {}
        circular["self"] = circular
        self.assertIsNone(find_mapping(circular, "id", 4))

    def test_api_storage_and_model_number_and_key_policies_remain_distinct(self):
        value = {None: float("nan"), 1: (float("inf"), -float("inf"), 1.25, True, None)}
        self.assertEqual(json_safe_value(value), {"1": [None, None, 1.25, True, None]})
        self.assertEqual(
            json_safe_api_value(value), {"None": None, "1": [None, None, 1.25, True, None]}
        )
        self.assertEqual(json_safe_storage_value(value), {"1": [0.0, 0.0, 1.25, True, None]})

    def test_depth_and_non_json_values_preserve_original_contract(self):
        self.assertEqual(json_safe_value({"a": {"b": "c"}}, depth=1), {"a": "{'b': 'c'}"})
        self.assertEqual(json_safe_value({"x"}), ["x"])
        self.assertEqual(json_safe_value(Path("example")), "example")
        self.assertEqual(json_safe_value("text"), "text")
        self.assertEqual(json_safe_value(False, depth=0), "False")

    def test_conversion_does_not_mutate_input(self):
        value = {"items": [{"nested": 3}]}
        converted = json_safe_storage_value(value)
        converted["items"][0]["nested"] = 5
        self.assertEqual(value["items"][0]["nested"], 3)

    def test_iterative_conversion_matches_previous_recursive_policy(self):
        def previous(value, depth, nonfinite, drop_none_keys):
            if depth <= 0:
                return str(value)
            if value is None or isinstance(value, (str, bool, int)):
                return value
            if isinstance(value, float):
                return value if math.isfinite(value) else nonfinite
            if isinstance(value, dict):
                return {
                    str(key): previous(item, depth - 1, nonfinite, drop_none_keys)
                    for key, item in value.items()
                    if key is not None or not drop_none_keys
                }
            if isinstance(value, (list, tuple, set)):
                return [previous(item, depth - 1, nonfinite, drop_none_keys) for item in value]
            return str(value)

        values = [
            None,
            True,
            42,
            "text",
            float("nan"),
            Path("example"),
            {1: {"nested": [1, None]}, "1": ["last collision"], None: float("inf")},
            (1, {"rows": [{"deep": [False, 3.25]}]}),
            {"a", "b"},
        ]
        circular = {"self": None}
        circular["self"] = circular
        values.append(circular)
        for value in values:
            for depth in (0, 1, 2, 4, 8, 12):
                for nonfinite, drop_none_keys in ((None, True), (0.0, True), (None, False)):
                    with self.subTest(
                        value_type=type(value).__name__,
                        depth=depth,
                        policy=(nonfinite, drop_none_keys),
                    ):
                        expected = previous(value, depth, nonfinite, drop_none_keys)
                        actual = json_safe_value(
                            value, depth=depth, nonfinite=nonfinite, drop_none_keys=drop_none_keys
                        )
                        self.assertEqual(actual, expected)
                        if isinstance(expected, dict):
                            self.assertEqual(list(actual), list(expected))

    def test_full_state_depth_is_iterative_and_preserves_leaf_types(self):
        value = 17
        for _ in range(512):
            value = {"child": value}
        converted = json_safe_storage_value(value, depth=513)
        for _ in range(512):
            converted = converted["child"]
        self.assertEqual(converted, 17)
        self.assertIs(type(converted), int)


if __name__ == "__main__":
    unittest.main()
