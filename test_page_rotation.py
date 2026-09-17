"""Rotation regressions; no OCR or vision model is invoked."""
from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from page_rotation import (
    choose_orientation, oriented_size, page_orientation, render_upright_page,
    rotate_bbox, scale_bbox,
)
from invoice_response import clean_invoice_response
from targeted_ocr import retry_regions
from visual_invoice import parse_invoice_visual, render_pages


class FakeImage:
    def __init__(self, width=600, height=800):
        self.width, self.height = width, height

    def crop(self, box):
        return FakeImage(max(1, box[2]-box[0]), max(1, box[3]-box[1]))

    def convert(self, mode):
        return self

    def thumbnail(self, size):
        return None

    def save(self, target, *args, **kwargs):
        if hasattr(target, "write"):
            target.write(b"image")


class FakePage:
    def get_width(self):
        return 216

    def get_height(self):
        return 288

    def close(self):
        return None


class FakeDocument:
    def __len__(self):
        return 1

    def get_page(self, index):
        return FakePage()

    def close(self):
        return None


class FakeBitmap:
    def to_pil(self):
        return FakeImage()

    def close(self):
        return None


class RecordingPage:
    def __init__(self):
        self.render_rotations = []

    def render(self, scale, rotation):
        self.render_rotations.append(rotation)
        return FakeBitmap()


class EmptyPredictor:
    def predict(self, path, **kwargs):
        return []


class PageRotationTests(unittest.TestCase):
    def test_box_round_trip_90_180_90_returns_to_origin(self):
        self.assertEqual(oriented_size(600, 800, 0), (600, 800))
        self.assertEqual(oriented_size(600, 800, 90), (800, 600))
        self.assertEqual(oriented_size(600, 800, 180), (600, 800))
        self.assertEqual(oriented_size(600, 800, 270), (800, 600))
        self.assertEqual(scale_bbox([10, 20, 30, 40], 200, 400), [20, 40, 60, 80])
        original = [20, 30, 120, 80]
        box, size = rotate_bbox(original, 600, 800, 90)
        box, size = rotate_bbox(box, *size, 180)
        box, size = rotate_bbox(box, *size, 90)
        self.assertEqual(box, original)
        self.assertEqual(size, (600, 800))

    def test_uncertain_confidence_is_not_applied_and_marks_only_affected_page(self):
        rejected = choose_orientation("90", .8999, .90)
        accepted = choose_orientation("90", .90, .90)
        self.assertEqual(rejected["rotation_degrees"], 0)
        self.assertEqual(rejected["rotation_candidate_degrees"], 90)
        self.assertEqual(rejected["rotation_status"], "uncertain")
        self.assertEqual(accepted["rotation_degrees"], 90)
        self.assertEqual(accepted["rotation_status"], "applied")

        fallback = {"data": {"supplier": {}, "invoice": {}, "customer": {},
                             "items": [], "totals": {}, "validation": {}},
                    "quality": {"needs_review": False, "missing_fields": [],
                                "field_evidence": {"invoice.invoice_number": {
                                    "page": 2, "text": "INV-2"}}}}
        pages = [
            {"page": 1, "rotation_status": "upright", "rotation_degrees": 0},
            {"page": 2, **rejected},
        ]
        with patch("visual_invoice.parse_invoice_hybrid", return_value=fallback):
            result = parse_invoice_visual("unused.pdf", pages, "x.pdf", "eng", mode="fast")
        self.assertTrue(result["quality"]["needs_review"])
        issue = result["quality"]["orientation_issues"][0]
        self.assertEqual(issue["page"], 2)
        self.assertEqual(issue["rotation_degrees"], 0)
        self.assertEqual(issue["affected_fields"], ["invoice.invoice_number"])

    def test_failed_detection_is_not_applied_and_marks_only_affected_page(self):
        failed = {"rotation_degrees": 0, "rotation_confidence": None,
                  "rotation_source": "paddle_doc_orientation", "rotation_status": "failed",
                  "rotation_error": "classifier unavailable"}
        fallback = {"data": {"supplier": {}, "invoice": {}, "customer": {},
                             "items": [], "totals": {}, "validation": {}},
                    "quality": {"needs_review": False, "missing_fields": [],
                                "field_evidence": {"items[0].amount": {
                                    "page": 2, "text": "20.00"}}}}
        pages = [{"page": 1, "rotation_status": "upright", "rotation_degrees": 0},
                 {"page": 2, **failed}]
        with patch("visual_invoice.parse_invoice_hybrid", return_value=fallback):
            result = parse_invoice_visual("unused.pdf", pages, "x.pdf", "eng", mode="fast")
        issue = result["quality"]["orientation_issues"][0]
        self.assertTrue(result["quality"]["needs_review"])
        self.assertEqual(issue["rotation_degrees"], 0)
        self.assertEqual(issue["affected_fields"], ["items[0].amount"])
        self.assertEqual(issue["reason"], "classifier unavailable")

    def test_pdf_rotate_and_nonzero_residual_are_applied_separately_once(self):
        pdf_page = RecordingPage()
        with patch("page_rotation.rotate_image", side_effect=lambda image, angle: image) as rotate:
            render_upright_page(pdf_page, 200, 90)
        self.assertEqual(pdf_page.render_rotations, [0])
        rotate.assert_called_once_with(unittest.mock.ANY, 90)

        orientation = page_orientation({"page": 1, "pdf_rotation_degrees": 270,
                                        "rotation_degrees": 90,
                                        "rotation_confidence": .98,
                                        "rotation_source": "paddle_doc_orientation",
                                        "rotation_status": "applied"})
        response = clean_invoice_response({
            "data": {}, "quality": {"needs_review": False},
            "page_orientations": [orientation],
        })
        self.assertEqual(orientation["pdf_rotation_degrees"], 270)
        self.assertEqual(orientation["rotation_degrees"], 90)
        self.assertEqual(response["page_orientations"], [orientation])

    def test_targeted_crop_and_ollama_images_use_same_rotation(self):
        targeted_angles = []
        visual_angles = []

        def targeted_render(page, dpi, angle):
            targeted_angles.append((dpi, angle))
            return FakeImage(800 if angle in (90, 270) else 600,
                             600 if angle in (90, 270) else 800)

        def visual_render(page, dpi, angle):
            visual_angles.append((dpi, angle))
            return FakeImage(800 if angle in (90, 270) else 600,
                             600 if angle in (90, 270) else 800)

        page_payload = {
            "page": 1, "render_dpi": 200, "canonical_width": 800,
            "canonical_height": 600, "rotation_degrees": 270, "words": [],
        }
        region = {"kind": "numeric_cell", "bbox": [10, 20, 100, 80], "original": {}}
        with tempfile.TemporaryDirectory() as directory, \
                patch("targeted_ocr.plan_regions", return_value=[region]), \
                patch("targeted_ocr.render_upright_page", side_effect=targeted_render):
            retry_regions(FakePage(), page_payload, lambda language='ar': EmptyPredictor(),
                          lambda result: [], Path(directory))

        words = [{"text": "Description", "left": 10, "top": 20, "width": 80, "height": 10},
                 {"text": "Total", "left": 10, "top": 140, "width": 40, "height": 10}]
        visual_page = dict(page_payload, words=words)
        with patch("visual_invoice.header_hint", return_value=25), \
                patch("visual_invoice.geometry", return_value=(10, lambda word: word["top"])), \
                patch("visual_invoice.render_upright_page", side_effect=visual_render), \
                patch.dict("sys.modules", {"pypdfium2": SimpleNamespace(
                    PdfDocument=lambda path: FakeDocument())}):
            rendered_pages = list(render_pages("unused.pdf", [visual_page]))
        self.assertEqual(len(rendered_pages[0][2]), 2)  # Full page + table crop sent to Ollama.
        self.assertTrue(targeted_angles)
        self.assertTrue(visual_angles)
        self.assertTrue(all(angle == 270 for _, angle in targeted_angles + visual_angles))

    def test_rotation_does_not_modify_arabic_or_urdu_rtl_text(self):
        words = ["فاتورة ضريبية", "اردو رسید"]
        box = [10, 20, 110, 50]
        rotated, size = rotate_bbox(box, 600, 800, 90)
        restored, _ = rotate_bbox(rotated, *size, 270)
        self.assertEqual(restored, box)
        self.assertEqual(words, ["فاتورة ضريبية", "اردو رسید"])

    def _assert_reference_pdf_is_upright_after_pdfium_rotation(self, name):
        try:
            import pypdfium2 as pdfium
        except ImportError:
            self.skipTest("PDFium is not installed in this Python")

        path = Path("public_invoice_pdfs") / name
        with pdfium.PdfDocument(str(path)) as document, closing(document[0]) as page:
            self.assertEqual(page.get_rotation(), 270)
            raw = render_upright_page(page, 72, 0)
            self.assertGreater(raw.height, raw.width)

    def test_reference_pdf_9498_uses_real_file_and_pdfium_consumes_rotate(self):
        self._assert_reference_pdf_is_upright_after_pdfium_rotation("9498.pdf")

    def test_reference_pdf_9609_uses_real_file_and_pdfium_consumes_rotate(self):
        self._assert_reference_pdf_is_upright_after_pdfium_rotation("9609.pdf")

    def test_reference_pdf_9480_uses_real_file_and_pdfium_consumes_rotate(self):
        self._assert_reference_pdf_is_upright_after_pdfium_rotation("9480.pdf")


if __name__ == "__main__":
    unittest.main()
