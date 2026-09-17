import io
import json
import sys
import unittest
from types import ModuleType
from unittest.mock import patch

from invoice_response import (
    TOP_LEVEL_KEYS, clean_invoice_response, error_invoice_response,
    response_json_bytes, validate_response_schema,
)
from ocr_web import Handler
from pdf_errors import InvalidPDFError
from validator import validate


class BrokenString:
    def __str__(self):
        raise RuntimeError("cannot stringify")


class JsonContractTests(unittest.TestCase):
    def test_success_and_error_use_the_same_validated_top_level_schema(self):
        success = clean_invoice_response({
            "pipeline_version": "test", "data": {},
            "quality": {"needs_review": False, "parser": "test"},
        })
        failure = error_invoice_response(500, "failed", {
            "pipeline_version": "test", "data": {"invoice": {"invoice_number": "INV-7"}},
            "quality": {"needs_review": True, "parser": "partial"},
        })
        self.assertEqual(tuple(success), TOP_LEVEL_KEYS)
        self.assertEqual(tuple(failure), TOP_LEVEL_KEYS)
        self.assertEqual(failure["data"]["invoice"]["invoice_number"], "INV-7")
        validate_response_schema(json.loads(response_json_bytes(success)))
        validate_response_schema(json.loads(response_json_bytes(failure)))

    def test_non_finite_and_unserializable_output_falls_back_to_valid_json(self):
        payload = {"bad_float": float("nan"), "bad_object": BrokenString()}
        decoded = json.loads(response_json_bytes(payload))
        self.assertEqual(decoded["status"], "error")
        validate_response_schema(decoded)

    def test_corrupt_pdf_returns_schema_valid_json_instead_of_crashing(self):
        boundary = "OCRBoundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="pdf"; filename="broken.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + b"%PDF-1.7\nnot a readable PDF\n" + f"\r\n--{boundary}--\r\n".encode()
        handler = object.__new__(Handler)
        handler.path = "/"
        handler.headers = {"Content-Length": str(len(body)),
                           "Content-Type": f"multipart/form-data; boundary={boundary}"}
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        statuses = []
        handler.send_response = statuses.append
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler.log_message = lambda *args: None
        fake_coordinate = ModuleType("coordinate_ocr")
        fake_coordinate.extract_pdf = lambda *args, **kwargs: (_ for _ in ()).throw(
            InvalidPDFError("PDF is corrupt or unreadable."))
        with patch.dict(sys.modules, {"coordinate_ocr": fake_coordinate}):
            handler.process_upload()
        response = json.loads(handler.wfile.getvalue())
        self.assertEqual(statuses, [400])
        self.assertEqual(response["status"], "error")
        self.assertIn("corrupt", response["error"]["message"])
        validate_response_schema(response)

    def test_financial_mismatch_retains_value_and_has_field_reason(self):
        document = {
            "items": [{"quantity": 2, "unit_price": 10, "discount": 0,
                       "taxable_amount": 19, "tax_rate_percent": 15,
                       "tax_amount": 2.85, "item_subtotal_including_vat": 21.85}],
            "totals": {"total_excluding_vat": 20,
                       "total_taxable_amount_excluding_vat": 19,
                       "total_vat": 2.85, "total_amount_including_vat": 999},
        }
        result = validate(document)
        self.assertEqual(document["totals"]["total_amount_including_vat"], 999)
        review = next(item for item in result["field_reviews"]
                      if item["field"] == "totals.total_amount_including_vat")
        self.assertTrue(review["needs_review"])
        self.assertTrue(review["reason"])
        self.assertEqual(review["actual"], 999.0)


if __name__ == "__main__":
    unittest.main()
