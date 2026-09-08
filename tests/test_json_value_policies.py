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


if __name__ == "__main__":
    unittest.main()
