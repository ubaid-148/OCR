"""Synthetic failure cases; these tests do not measure real invoice accuracy."""
import json
import unittest
from unittest.mock import patch

from llm_extractor import _json_response, extract_with_ollama
from main import merge_drafts
from validator import validate


class ExtractionSafetyTests(unittest.TestCase):
    def test_reordered_rows_do_not_mix_prices(self):
        rule = {"items": [{"item_id": "A", "unit_price": None}, {"item_id": "B", "unit_price": None}]}
        ai = {"items": [{"item_id": "B", "unit_price": 20}, {"item_id": "A", "unit_price": 10}]}
        result = merge_drafts(rule, ai)
        self.assertEqual(result["items"], rule["items"])
        self.assertFalse(result["validation"]["passed"])

    def test_extra_ai_row_is_not_appended(self):
        rule = {"items": [{"item_id": "A", "quantity": 1}]}
        ai = {"items": [{"item_id": "A", "quantity": 1}, {"item_id": "B", "quantity": 9}]}
        self.assertEqual(merge_drafts(rule, ai)["items"], rule["items"])

    def test_duplicate_item_codes_are_ambiguous(self):
        rule = {"items": [{"item_id": "A"}, {"item_id": "A"}]}
        ai = {"items": [{"item_id": "A", "quantity": 1}, {"item_id": "A", "quantity": 9}]}
        self.assertEqual(merge_drafts(rule, ai)["items"], rule["items"])

    def test_unanchored_rows_are_not_joined(self):
        rule = {"items": [{"quantity": None}]}
        self.assertEqual(merge_drafts(rule, {"items": [{"quantity": 9}]})["items"], rule["items"])

    def test_aligned_unique_rows_can_fill_with_review(self):
        result = merge_drafts({"items": [{"item_id": "A", "quantity": None}]},
                              {"items": [{"item_id": "A", "quantity": 2}]})
        self.assertEqual(result["items"][0]["quantity"], 2)
        self.assertTrue(any("filled_by_llm" in w for w in result["validation"]["warnings"]))

    def test_existing_review_reason_survives_ai_merge(self):
        result = merge_drafts({"validation": {"passed": False, "warnings": ["uncertain orientation"]}}, {})
        self.assertIn("uncertain orientation", result["validation"]["warnings"])

    def test_unavailable_ai_reason_survives_without_draft(self):
        result = merge_drafts({}, None, ["llm_unavailable"])
        self.assertIn("llm_unavailable", result["validation"]["warnings"])

    def test_identifier_leading_zero_difference_is_reported(self):
        result = merge_drafts({"invoice_number": "00123"}, {"invoice_number": "123"})
        self.assertTrue(any("mismatch" in w for w in result["validation"]["warnings"]))

    def test_large_identifier_difference_is_reported(self):
        result = merge_drafts({"invoice_number": "99999999999999991"}, {"invoice_number": "99999999999999992"})
        self.assertTrue(any("mismatch" in w for w in result["validation"]["warnings"]))

    def test_nonfinite_operands_require_review_without_crashing(self):
        for value in ("NaN", "Infinity", "-Infinity", "sNaN"):
            with self.subTest(value=value):
                result = validate({"items": [{"quantity": value, "unit_price": 10, "taxable_amount": 20,
                                              "tax_rate_percent": 15, "tax_amount": 3,
                                              "item_subtotal_including_vat": 23}], "totals": {}})
                self.assertTrue(result["needs_review"])
                self.assertTrue(any("items[0].quantity" in w.get("missing_operands", []) for w in result["warnings"]))

    def test_nonfinite_ai_json_is_rejected(self):
        for number in ("NaN", "Infinity", "1e309"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                _json_response('{"items": [{"quantity": ' + number + '}]}')

    def test_invalid_ai_container_is_rejected(self):
        for draft in ({"items": "oops"}, {"items": [1]}, {"totals": []}, {"validation": None}):
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                _json_response(json.dumps(draft))

    def test_truncated_ai_response_is_rejected(self):
        with patch("llm_extractor.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"response": "{}", "done": True, "done_reason": "length"}).encode()
            with self.assertRaises(ValueError):
                extract_with_ollama([], "test-model")


if __name__ == "__main__":
    unittest.main()
