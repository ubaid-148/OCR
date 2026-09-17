import json
import unittest
from pathlib import Path

from canonical_schema import to_canonical


TOP_LEVEL = ["document_type", "invoice_number", "invoice_serial", "invoice_date", "date_of_supply",
             "reference_no", "payment_method", "seller", "customer", "items", "totals", "vat_summary",
             "currency", "page_info", "validation"]
SELLER = ["name_ar", "name_en", "tax_number", "commercial_registration", "building_no", "street", "district",
          "postal_code", "additional_no", "short_address", "city", "country"]
CUSTOMER = ["name_ar", "customer_code", "tax_number", "commercial_registration", "building_no", "street", "district",
            "postal_code", "additional_no", "short_address", "city", "country", "customer_balance"]
ITEM = ["item_id", "item_name", "unit", "quantity", "unit_price", "discount", "taxable_amount", "tax_rate_percent",
        "tax_code", "tax_amount", "item_subtotal_including_vat"]
TOTALS = ["total_excluding_vat", "discount", "other_charges", "total_taxable_amount_excluding_vat", "total_vat",
          "total_vat_rate_percent", "total_amount_including_vat", "amount_in_words_ar"]


class CanonicalSchemaTests(unittest.TestCase):
    def complete_document(self):
        # Populate every field so missing-value warnings cannot mask review loss.
        return {
            "invoice_number": "INV-123", "invoice_serial": "123",
            "invoice_date": "2026-09-17", "date_of_supply": "2026-09-17",
            "reference_no": "REF-123", "payment_method": "cash",
            "seller": dict.fromkeys(SELLER, "printed"),
            "customer": dict.fromkeys(CUSTOMER, "printed"),
            "items": [dict.fromkeys(ITEM, "printed")],
            "totals": dict.fromkeys(TOTALS, "printed"),
            "vat_summary": dict.fromkeys(("tax_code", "before_tax", "tax_amount", "including_tax"), "printed"),
            "currency": "SAR", "validation": {"passed": True, "warnings": []},
        }

    def test_explicit_review_survives_without_other_validation_failures(self):
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                source = self.complete_document()
                source["validation"]["needs_review"] = True
                document = to_canonical({"data": source} if wrapped else source)
                self.assert_shape(document)
                self.assertFalse(document["validation"]["passed"])
                self.assertIn("needs_review: upstream validation requires review",
                              document["validation"]["warnings"])
                self.assertEqual(to_canonical(document), document)
                self.assertEqual(source["validation"]["warnings"], [])

    def test_complete_accepted_document_still_passes(self):
        source = self.complete_document()
        source["validation"]["needs_review"] = False
        self.assertEqual(to_canonical(source)["validation"], {"passed": True, "warnings": []})

    def test_review_preserves_existing_reasons(self):
        source = self.complete_document()
        source["validation"].update(needs_review=True, warnings=["Check invoice number"])
        validation = to_canonical(source)["validation"]
        self.assertFalse(validation["passed"])
        self.assertIn("Check invoice number", validation["warnings"])

    def assert_shape(self, document):
        self.assertEqual(list(document), TOP_LEVEL)
        self.assertEqual(list(document["seller"]), SELLER)
        self.assertEqual(list(document["customer"]), CUSTOMER)
        self.assertEqual(list(document["totals"]), TOTALS)
        self.assertEqual(list(document["vat_summary"]), ["tax_code", "before_tax", "tax_amount", "including_tax"])
        self.assertEqual(list(document["page_info"]), ["page", "total_pages"])
        self.assertEqual(list(document["validation"]), ["passed", "warnings"])
        for item in document["items"]:
            self.assertEqual(list(item), ITEM)

    def test_two_different_templates_have_identical_schema(self):
        fixtures = Path("tests/fixtures")
        expected = Path("tests/expected")
        for fixture in (fixtures / "template_alpha.json", fixtures / "template_beta.json"):
            document = to_canonical(json.loads(fixture.read_text(encoding="utf-8")))
            expected_document = json.loads((expected / fixture.name).read_text(encoding="utf-8"))
            self.assert_shape(document)
            self.assertEqual(document["document_type"], expected_document["document_type"])
            self.assertEqual(document["invoice_number"], expected_document["invoice_number"])
            self.assertEqual(document["items"][0]["item_id"], expected_document["items"][0]["item_id"])
            self.assertTrue(any("needs_review" in warning for warning in document["validation"]["warnings"]))

    def test_missing_numeric_values_are_null_and_reviewed(self):
        document = to_canonical({"items": [{"item_code": "X"}]})
        self.assertIsNone(document["items"][0]["quantity"])
        self.assertIsNone(document["totals"]["total_vat"])
        self.assertFalse(document["validation"]["passed"])
        self.assertIn("needs_review: missing items[0].quantity", document["validation"]["warnings"])


if __name__ == "__main__":
    unittest.main()
