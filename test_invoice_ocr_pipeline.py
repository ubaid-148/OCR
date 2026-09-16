import json
import unittest
from pathlib import Path

from bbox_grouping import group_rows
from main import build_document
from table_extractor import extract_table, number
from validator import validate


def box(text, left, top, width=50, height=24, confidence=99, page=1):
    return {"page": page, "text": text, "confidence": confidence,
            "bbox": {"left": left, "top": top, "width": width, "height": height}}


class InvoiceOcrPipelineTests(unittest.TestCase):
    def test_number_normalizes_arabic_digits_commas_and_tax_code_punctuation(self):
        self.assertEqual(number("34,02"), 34.02)
        self.assertEqual(number("١٤٫٧٩"), 14.79)

    def test_group_rows_uses_vertical_tolerance(self):
        rows = group_rows([box("A", 0, 100), box("B", 100, 116), box("C", 0, 160)])
        self.assertEqual([[item["text"] for item in row] for row in rows], [["A", "B"], ["C"]])

    def test_dynamic_table_buckets_columns_and_preserves_evidence(self):
        headers = [box("Item ID", 100, 100), box("Quantity", 300, 100), box("Unit Price", 500, 100),
                   box("Taxable Amount", 700, 100), box("Tax Rate", 900, 100), box("Tax Amount", 1100, 100),
                   box("Including VAT", 1300, 100)]
        row = [box("A-1", 100, 160), box("2", 300, 160), box("14,79", 500, 160), box("29.58", 700, 160),
               box("15%", 900, 160), box("4.44", 1100, 160), box("34.02", 1300, 160)]
        items, meta = extract_table(headers + row)
        self.assertTrue(meta["needs_review"])
        self.assertIn("unknown_template", meta["warning"])
        self.assertEqual(items[0]["quantity"], 2.0)
        self.assertEqual(items[0]["unit_price"], 14.79)
        self.assertEqual(items[0]["tax_rate_percent"], 15.0)
        self.assertEqual(items[0]["evidence"]["unit_price"]["text"], "14,79")

    def test_validator_does_not_correct_inconsistent_values(self):
        result = validate({"items": [{"quantity": 2, "unit_price": 10, "discount": 0,
                                       "taxable_amount": 19, "tax_rate_percent": 15,
                                       "tax_amount": 2.85, "item_subtotal_including_vat": 21.85}],
                           "totals": {"total_amount_including_vat": 21.85,
                                      "total_taxable_amount_excluding_vat": 19, "total_vat": 2.85}})
        self.assertFalse(result["passed"])
        self.assertTrue(result["warnings"][0]["needs_review"])

    def test_supplied_reference_totals_reconcile(self):
        reference = json.loads(Path("tools/references/9498_user_reference.json").read_text(encoding="utf-8"))
        items = [{
            "quantity": row["quantity"], "unit_price": row["unit_price"], "discount": 0,
            "taxable_amount": row["amount"], "tax_rate_percent": float(str(row["vat_rate"]).rstrip("%")),
            "tax_amount": row["vat_amount"], "item_subtotal_including_vat": row["gross_amount"],
        } for row in reference["items"]]
        result = validate({"items": items, "totals": {
            "total_amount_including_vat": reference["totals"]["net_amount"],
            "total_taxable_amount_excluding_vat": reference["totals"]["subtotal"],
            "total_vat": reference["totals"]["vat_amount"],
        }})
        self.assertEqual(reference["totals"]["net_amount"], 109.03)
        self.assertTrue(result["passed"])

    def test_build_document_exposes_requested_top_level_schema(self):
        document = build_document([box("Invoice Number", 100, 10), box("INV-7", 300, 10)])
        required = {"document_type", "invoice_number", "invoice_serial", "invoice_date", "date_of_supply",
                    "reference_no", "payment_method", "seller", "customer", "items", "totals", "vat_summary",
                    "currency", "validation"}
        self.assertTrue(required.issubset(document))
        self.assertIn("passed", document["validation"])


if __name__ == "__main__":
    unittest.main()