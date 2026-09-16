"""Fast diagnostics tests; OCR and models are stubbed, never run locally."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from tools.ocr_diagnostics import _pdf, _selected_page, run_raw


class DiagnosticsTests(unittest.TestCase):
    def test_pdf_selection_accepts_any_valid_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "my-new-layout.pdf"
            path.write_bytes(b"%PDF-1.7\n")
            self.assertEqual(_pdf(path), path.resolve())

    def test_pdf_selection_rejects_non_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invoice.pdf"
            path.write_bytes(b"not a pdf")
            with self.assertRaisesRegex(ValueError, "Not a PDF"):
                _pdf(path)

    def test_page_selection_checks_bounds(self) -> None:
        self.assertEqual(_selected_page(["first", "second"], 2), "second")
        with self.assertRaisesRegex(ValueError, "outside this PDF"):
            _selected_page(["first"], 2)

    def test_raw_writes_evidence_for_chosen_filename_without_ocr(self) -> None:
        ocr = types.ModuleType("coordinate_ocr")
        ocr.extract_pdf = lambda pdf, language, progress: {
            "device": "gpu:0", "pages": [{"page": 1, "words": [
                {"text": "123.45", "confidence": 95, "left": 1, "top": 2,
                 "width": 3, "height": 4}]}]}
        parser = types.ModuleType("local_ai_parser")
        parser.parse_invoice_hybrid = lambda pages, filename, language, mode: {
            "data": {"source_filename": filename, "items": [], "totals": {}},
            "quality": {"parser": "spatial_fast"},
        }
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(sys.modules, {"coordinate_ocr": ocr, "local_ai_parser": parser}), \
                patch.dict(os.environ, {}, clear=False), \
                contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / "out"
            output.mkdir()
            run_raw(Path(directory) / "chosen.pdf", output)
            raw = json.loads((output / "raw_paddleocr.json").read_text(encoding="utf-8"))
            view = json.loads((output / "parser_fast.json").read_text(encoding="utf-8"))
            full = json.loads((output / "parser_full.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["benchmark"]["input"], "chosen.pdf")
            self.assertEqual(view["parser"], "spatial_fast")
            self.assertEqual(full["data"]["source_filename"], "chosen.pdf")


if __name__ == "__main__":
    unittest.main()
