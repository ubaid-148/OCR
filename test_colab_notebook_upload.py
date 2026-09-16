"""Exercise the Colab upload cell contract without running a model locally."""
from __future__ import annotations

from contextlib import redirect_stdout
from email import policy
from email.parser import BytesParser
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class ColabUploadTests(unittest.TestCase):
    def test_upload_runs_raw_paddleocr_without_reference_or_parser(self) -> None:
        notebook = json.loads(Path("colab_setup.ipynb").read_text(encoding="utf-8"))
        cell = next(cell for cell in notebook["cells"]
                    if "Upload one PDF and run raw PaddleOCR" in "".join(cell.get("source", [])))
        source = "".join(cell["source"])
        self.assertIn("files.upload()", source)
        self.assertIn("coordinate_ocr.py", source)
        self.assertIn("raw_paddleocr.json", source)
        self.assertIn("confidence", source)
        self.assertIn("page", source)
        self.assertNotIn("REFERENCE_JSON", source)
        self.assertNotIn("parse_invoice", source)
        self.assertNotIn("ocr_web.py", source)


if __name__ == "__main__":
    unittest.main()
