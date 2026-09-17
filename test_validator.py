"""Unknown arithmetic operands must remain review-required, never become zero."""
from copy import deepcopy
import unittest

from validator import TOLERANCE, validate
from invoice_response import clean_invoice_response


class NullOperandTests(unittest.TestCase):
    def document(self):
        return {
            "items": [{"quantity": 2, "unit_price": 10, "discount": 0,
                       "taxable_amount": 20, "tax_rate_percent": 15,
                       "tax_amount": 3, "item_subtotal_including_vat": 23}],
            "totals": {"total_excluding_vat": 20, "discount": 0, "other_charges": 0,
                       "total_taxable_amount_excluding_vat": 20, "total_vat": 3,
                       "total_vat_rate_percent": 15, "total_amount_including_vat": 23},
        }

    def test_complete_values_still_pass_without_tolerance_change(self):
        self.assertEqual(str(TOLERANCE), "0.05")
        self.assertTrue(validate(self.document())["passed"])

    def test_each_null_operand_is_unresolved_not_passed(self):
        for section in ("items", "totals"):
            keys = self.document()[section][0] if section == "items" else self.document()[section]
            for key in keys:
                with self.subTest(section=section, key=key):
                    document = self.document()
                    container = document[section][0] if section == "items" else document[section]
                    container[key] = None
                    original = deepcopy(document)
                    result = validate(document)
                    path = f"items[0].{key}" if section == "items" else f"totals.{key}"
                    unresolved = [r for r in result["field_reviews"] if r.get("status") == "unresolved"]
                    self.assertFalse(result["passed"])
                    self.assertTrue(result["needs_review"])
                    self.assertTrue(any(path in r["missing_operands"] for r in unresolved))
                    self.assertEqual(document, original)

    def test_unknown_item_tax_does_not_reconcile_as_zero(self):
        document = self.document()
        document["items"][0]["tax_amount"] = None
        document["totals"]["total_vat"] = 0
        result = validate(document)
        review = next(r for r in result["field_reviews"] if r["field"] == "totals.total_vat")
        self.assertEqual(review["status"], "unresolved")
        self.assertIn("items[0].tax_amount", review["missing_operands"])
        self.assertNotIn("expected", review)

    def test_zero_quantity_is_not_replaced_by_one(self):
        document = self.document()
        document["items"][0].update(quantity=0, taxable_amount=0, tax_amount=0,
                                    item_subtotal_including_vat=0)
        document["totals"].update(total_excluding_vat=0, total_taxable_amount_excluding_vat=0,
                                  total_vat=0, total_amount_including_vat=0)
        self.assertTrue(validate(document)["passed"])

    def test_partial_sum_does_not_validate_document_total(self):
        document = self.document()
        document["items"].append(dict(document["items"][0], taxable_amount=None))
        result = validate(document)
        review = next(r for r in result["field_reviews"] if r["field"] == "totals.total_taxable_amount_excluding_vat")
        self.assertEqual(review["status"], "unresolved")

    def test_unknown_quantity_leaves_extended_gross_and_vat_unresolved(self):
        document = self.document()
        document["items"][0].update(quantity=None, item_subtotal_including_vat=46)
        document["totals"]["total_vat"] = 6
        result = validate(document)
        for field in ("items[0].item_subtotal_including_vat", "totals.total_vat"):
            review = next(r for r in result["field_reviews"] if r["field"] == field)
            self.assertEqual(review["status"], "unresolved")
            self.assertIn("items[0].quantity", review["missing_operands"])

    def test_empty_items_are_unresolved_even_for_zero_totals(self):
        document = self.document()
        document["items"] = []
        document["totals"]["total_vat"] = 0
        self.assertFalse(validate(document)["passed"])

    def test_unresolved_check_reaches_public_field_reviews(self):
        document = self.document()
        document["items"][0]["quantity"] = None
        response = clean_invoice_response({"data": {"validation": validate(document)},
                                           "quality": {"needs_review": False}})
        self.assertEqual(response["status"], "needs_review")
        self.assertTrue(any("unresolved" in r["reason"] for r in response["field_reviews"]))
        review = next(r for r in response["field_reviews"] if r.get("status") == "unresolved")
        self.assertIn("items[0].quantity", review["missing_operands"])


if __name__ == "__main__":
    unittest.main()
