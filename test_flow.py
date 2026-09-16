import copy
import io
import json
import os
import unittest
from contextlib import closing
from unittest.mock import patch

from invoice_formatter import parse_invoice
from invoice_response import clean_invoice_response
from local_ai_parser import parse_invoice_hybrid, _validate
from native_pdf import extract_native_words
from ocr_web import Handler, accuracy_gpu_configured
from test_invoice_evidence import sample, pages


class FlowTests(unittest.TestCase):
    def test_accuracy_mode_refuses_cpu_when_colab_requires_gpu(self):
        with patch.dict(os.environ, {'VISION_REQUIRE_GPU': 'true', 'OCR_DEVICE': 'cpu'}):
            self.assertFalse(accuracy_gpu_configured('auto'))
            self.assertTrue(accuracy_gpu_configured('fast'))
        with patch.dict(os.environ, {'VISION_REQUIRE_GPU': 'true', 'OCR_DEVICE': 'gpu:0'}):
            self.assertTrue(accuracy_gpu_configured('auto'))

    def test_absent_currency_and_rate_are_not_assumed(self):
        totals = parse_invoice([], "empty.pdf", "eng")["data"]["totals"]
        self.assertIsNone(totals["currency"])
        self.assertIsNone(totals["vat_rate"])

    def test_legacy_receives_only_first_page(self):
        source = pages() * 2
        with patch("local_ai_parser.parse_invoice", wraps=parse_invoice) as legacy, patch("local_ai_parser.parse_layout", return_value=None):
            result = parse_invoice_hybrid(source, "multi.pdf", "eng", mode="fast")
        self.assertEqual(len(legacy.call_args.args[0]), 1)
        self.assertTrue(result["quality"]["needs_review"])
        self.assertIn("document.remaining_pages", result["quality"]["missing_fields"])

    def test_low_confidence_layout_requires_review(self):
        source = pages()
        source[0]["words"][-1]["confidence"] = 20
        with patch("local_ai_parser.parse_layout", return_value=sample()):
            result = parse_invoice_hybrid(source, "test.pdf", "eng", mode="fast")
        self.assertTrue(result["quality"]["needs_review"])
        self.assertTrue(result["quality"]["low_confidence_fields"])

    def test_checks_passed_does_not_claim_verified(self):
        self.assertEqual(_validate(sample())[1]["overall_status"], "checks_passed")

    def test_clean_response_exposes_pipeline_and_parser(self):
        payload={"pipeline_version":"test-flow","ocr_device":"gpu:0",
                 "timings_seconds":{"targeted_ocr":1.25},"data":{},
                 "quality":{"needs_review":True,"parser":"spatial_fast"}}
        result=clean_invoice_response(payload)
        self.assertEqual(result["pipeline_version"],"test-flow")
        self.assertEqual(result["parser"],"spatial_fast")
        self.assertEqual(result["ocr_device"],"gpu:0")
        self.assertEqual(result["timings_seconds"]["targeted_ocr"],1.25)

    def test_clean_response_exposes_ai_failure_reason(self):
        payload={"data":{},"quality":{"needs_review":True,"parser":"spatial_fallback",
                                       "local_ai_status":"failed","local_ai_error":"timed out"}}
        result=clean_invoice_response(payload)
        self.assertEqual(result["local_ai_error"],"timed out")

    def test_abbreviated_customer_label_is_not_a_customer_name(self):
        source=pages()
        source[0]['words'] += [
            {'text':'Cust.Name','left':100,'top':180,'width':100,'height':20,'confidence':99},
            {'text':'Example Buyer Trading Co.','left':260,'top':180,'width':240,'height':20,'confidence':99},
        ]
        with patch('local_ai_parser._ask_ollama',side_effect=OSError('offline')):
            result=parse_invoice_hybrid(source,'example.pdf','eng+ara')
        self.assertNotEqual(result['data']['customer']['name'],'Cust.Name')

    def test_json_error_preserves_message(self):
        handler = object.__new__(Handler)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: None
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler.send_failure("Only PDF files are supported.", 400)
        result = json.loads(handler.wfile.getvalue())
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["message"], "Only PDF files are supported.")

    def test_real_pdf_native_text_coordinates(self):
        try:
            import pypdfium2 as pdfium
        except ImportError:
            self.skipTest("PDFium not installed in this Python")
        content = b'BT /F1 12 Tf 40 750 Td (Example Supplier Company) Tj 0 -30 Td (Invoice Number INV-001) Tj 0 -30 Td (Description Quantity Price Amount) Tj 0 -30 Td (Steel hinges 2 50 100) Tj ET'
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                   b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                   b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                   b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
        data = b'%PDF-1.4\n'
        offsets = [0]
        for i, obj in enumerate(objects, 1):
            offsets.append(len(data))
            data += str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
        xref = len(data)
        data += b'xref\n0 6\n0000000000 65535 f \n'
        for offset in offsets[1:]:
            data += f'{offset:010d} 00000 n \n'.encode()
        data += b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF'
        with pdfium.PdfDocument(data) as doc:
            with closing(doc[0]) as page:
                words = extract_native_words(page)
                self.assertGreaterEqual(len(words), 4)
                self.assertIn("INV-001", " ".join(w["text"] for w in words))
                self.assertTrue(all(w["top"] > 0 and w["confidence"] is None for w in words))
                page.set_rotation(90)
                self.assertEqual(extract_native_words(page), [])


if __name__ == "__main__":
    unittest.main()
