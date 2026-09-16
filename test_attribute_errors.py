"""Deterministic tests for the offline source-error attribution harness."""
import tempfile
import unittest
from pathlib import Path

from tools.attribute_errors import attribute, canonicalize, normalized


def word(text, x, y, width=12, height=8):
    return {"text": text, "left": x, "top": y, "width": width,
            "height": height, "confidence": 95}


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pdf = Path(self.temp.name) / "example.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\nsynthetic fixture\n")

    def classify(self, truth, prediction, words, spatial=None):
        raw = {"pages": [{"page": 1, "words": words}]}
        return attribute(self.pdf, truth, raw, prediction, spatial)

    def test_ocr_missing_requires_verified_empty_region(self):
        truth = {"items": [{"item_code": "A1", "quantity": 2}],
                 "regions": {"items[0].quantity": {"page": 1, "bbox": [30, 10, 12, 8]}}}
        result = self.classify(truth, {"items": [{"item_code": "A1", "quantity": None}]},
                               [word("A1", 0, 10)])
        self.assertEqual(result["mismatches"][0]["bucket"], "OCR_MISSING")

    def test_ocr_character_error_requires_overlapping_box(self):
        truth = {"items": [{"item_code": "A1", "quantity": 2}],
                 "regions": {"items[0].quantity": {"page": 1, "bbox": [30, 10, 12, 8]}}}
        result = self.classify(truth, {"items": [{"item_code": "A1", "quantity": None}]},
                               [word("A1", 0, 10), word("Z", 30, 10)])
        self.assertEqual(result["mismatches"][0]["bucket"], "OCR_CHAR_ERROR")

    def test_correct_token_in_row_is_parser_column(self):
        truth = {"items": [{"item_code": "A1", "quantity": 2}]}
        result = self.classify(truth, {"items": [{"item_code": "A1", "quantity": 3}]},
                               [word("A1", 0, 10), word("2", 30, 10)])
        self.assertEqual(result["mismatches"][0]["bucket"], "PARSER_COLUMN")

    def test_right_column_wrong_row_is_parser_row(self):
        truth = {"items": [{"item_code": "A1", "quantity": 2},
                           {"item_code": "A2", "quantity": 5}]}
        prediction = {"data": {"items": [
            {"item_code": "A1", "quantity": 5},
            {"item_code": "A2", "quantity": 2, "field_evidence": {
                "quantity": {"text": "2", "bbox": [30, 10, 12, 8]}}},
        ]}}
        result = self.classify(truth, prediction,
                               [word("A1", 0, 10), word("2", 30, 10),
                                word("A2", 0, 30), word("5", 30, 30)])
        self.assertEqual(result["mismatches"][0]["bucket"], "PARSER_ROW")

    def test_vision_override_takes_precedence(self):
        truth = {"items": [{"item_code": "A1", "quantity": 2}]}
        spatial = {"items": [{"item_code": "A1", "quantity": 2}]}
        result = self.classify(truth, {"items": [{"item_code": "A1", "quantity": 3}]},
                               [word("A1", 0, 10), word("2", 30, 10)], spatial)
        self.assertEqual(result["mismatches"][0]["bucket"], "VISION_OVERRIDE")

    def test_unsupported_computed_gross_is_arithmetic(self):
        truth = {"items": [{"item_code": "A1", "quantity": 1,
                            "unit_price": 15, "amount": 15,
                            "vat_amount": 2.25, "gross_amount": 17.25}]}
        prediction = {"items": [{"item_code": "A1", "quantity": 1,
                                 "unit_price": 15, "amount": 15,
                                 "vat_amount": 2.25, "gross_amount": 99}]}
        result = self.classify(truth, prediction,
                               [word("A1", 0, 10), word("1", 20, 10),
                                word("15", 40, 10), word("2.25", 60, 10)])
        self.assertEqual(result["mismatches"][0]["bucket"], "ARITHMETIC_INCONSISTENT")

    def test_absent_token_without_region_is_not_guessed(self):
        truth = {"items": [{"item_code": "A1", "quantity": 2}]}
        result = self.classify(truth, {"items": [{"item_code": "A1", "quantity": None}]},
                               [word("A1", 0, 10), word("Z", 30, 10)])
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["mismatches"][0]["bucket"])
        self.assertEqual(result["unresolved"], 1)

    def test_cloud_aliases_and_arabic_digits(self):
        data = canonicalize({"line_items": [{"item_id": "١٢١٢", "taxable_amount": 14.79}],
                             "totals": {"total_vat": 2.22}})
        self.assertEqual(data["items"][0]["item_code"], "١٢١٢")
        self.assertEqual(data["totals"]["vat_amount"], 2.22)
        self.assertEqual(normalized("١٢١٢"), normalized("1212"))


if __name__ == "__main__":
    unittest.main()
