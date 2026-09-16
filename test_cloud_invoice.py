import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_invoice import extract_invoice_claude, strict_invoice_schema


class CloudInvoiceTests(unittest.TestCase):
    def test_full_schema_has_no_optional_or_union_fields(self):
        schema = strict_invoice_schema()
        self.assertIn("supplier", schema["required"])
        self.assertIn("items", schema["required"])
        self.assertIn("building_no", schema["properties"]["customer"]["required"])
        self.assertIn("quantity", schema["properties"]["items"]["items"]["required"])

        def inspect(node):
            self.assertNotIsInstance(node.get("type"), list)
            if node.get("type") == "object":
                self.assertEqual(set(node["properties"]), set(node["required"]))
                for child in node["properties"].values():
                    inspect(child)
            elif node.get("type") == "array":
                inspect(node["items"])

        inspect(schema)

    def test_extracts_pdf_and_preserves_printed_per_unit_item(self):
        raw = {
            "supplier": {"name_ar": "شركة اختبار", "vat_number": "310981818100003"},
            "invoice": {"invoice_number": "2690111862", "date": "2026-03-09",
                        "date_of_supply": "2026-04-02"},
            "customer": {"name_ar": "عميل", "vat_number": "300402905100003"},
            "items": [{"item_code": "1212", "description": "زيت شل هيلكس HELIX 15/40",
                       "quantity": "2.00", "unit_price": "14.79", "amount": "14.79",
                       "vat_amount": "2.22", "gross_amount": "34.02"}],
            "totals": {"subtotal": "29.58", "vat_rate": "15", "vat_amount": "4.44",
                       "net_amount": "34.02"},
        }
        message = {"model": "claude-sonnet-5", "stop_reason": "end_turn",
                   "content": [{"type": "text", "text": json.dumps(raw, ensure_ascii=False)}],
                   "usage": {"input_tokens": 100, "output_tokens": 200}}
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "9498.pdf"
            pdf.write_bytes(b"%PDF-1.4\ntest")
            with patch("cloud_invoice.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(message).encode())) as opened:
                result = extract_invoice_claude(pdf, "secret")
            sent = json.loads(opened.call_args.args[0].data)
        self.assertEqual(sent["messages"][0]["content"][0]["type"], "document")
        self.assertEqual(result["parser"], "claude_pdf")
        self.assertEqual(result["data"]["items"][0]["quantity"], 2.0)
        self.assertEqual(result["data"]["items"][0]["amount"], 14.79)
        self.assertEqual(result["data"]["invoice"]["date_of_supply"], "2026-04-02")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["api_usage"]["input_tokens"], 100)

    def test_rejects_truncated_response(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "one.pdf"
            pdf.write_bytes(b"%PDF-1.4\ntest")
            message = {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{}"}]}
            with patch("cloud_invoice.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(message).encode())):
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    extract_invoice_claude(pdf, "secret")


if __name__ == "__main__":
    unittest.main()
