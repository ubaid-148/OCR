import os
import unittest
from unittest.mock import patch

from invoice_formatter import contains, parse_invoice
from local_ai_parser import parse_invoice_hybrid


def word(text, top, left=100):
    return dict(text=text, top=top, left=left, width=250, height=20, confidence=99)


class InvoiceParsingTests(unittest.TestCase):
    def test_labels_do_not_match_returns_policy(self):
        self.assertFalse(contains("Customers may return goods", ("customer",)))
        self.assertTrue(contains("Customer VAT", ("customer",)))
        self.assertTrue(contains("UnitPrice", ("unit price",)))
        self.assertTrue(contains("رقم الفاتورة", ("رقم الفاتورة",)))
        self.assertFalse(contains("نص آخر", ("رقم الفاتورة",)))

    def test_footer_does_not_become_supplier_or_customer_name(self):
        pages = [{"words": [
            word("مؤسسة للتجارة", 20), word("وصف البضائع", 400),
            word("Customers may return goods", 900),
            word("بشرط أن تكون مرفقة بالفاتورة الأصلية", 920),
            word("for MUHAMMAD ABDULLAH AL AKKAS TRADING EST", 1000),
        ]}]
        result = parse_invoice(pages, "sample.pdf", "eng+ara")
        self.assertIsNone(result["data"]["supplier"]["name_ar"])
        self.assertIsNone(result["data"]["supplier"]["name_en"])
        self.assertIsNone(result["data"]["customer"]["name"])
        self.assertIn("items", result["quality"]["missing_fields"])
        self.assertIn("invoice.date", result["quality"]["missing_fields"])

    def test_supplier_arabic_name_is_local_to_header(self):
        pages = [{"words": [word("نص بعيد", 10),
                              word("مؤسسة للتجارة", 200),
                              word("EXAMPLE TRADING EST", 230)]}]
        result = parse_invoice(pages, "sample.pdf", "eng+ara")
        self.assertEqual(result["data"]["supplier"]["name_ar"], "مؤسسة للتجارة")

    def test_balanced_attempts_ai_for_missing_items(self):
        with patch.dict(os.environ, {"USE_LOCAL_AI": "true"}), \
             patch("local_ai_parser._ask_ollama", side_effect=OSError("offline")) as ai:
            result = parse_invoice_hybrid([], "sample.pdf", "eng+ara")
        ai.assert_called_once()
        self.assertTrue(result["quality"]["needs_review"])
        self.assertEqual(result["quality"]["parser"], "spatial_fallback")

    def test_fast_does_not_call_ai_and_explains_incomplete_output(self):
        with patch("local_ai_parser._ask_ollama") as ai:
            result = parse_invoice_hybrid([], "sample.pdf", "eng+ara", mode="fast")
        ai.assert_not_called()
        self.assertIn("review_message", result["quality"])


if __name__ == "__main__":
    unittest.main()
