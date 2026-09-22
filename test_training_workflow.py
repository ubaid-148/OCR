"""Data correctness tests; no GPU or model download is required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from training.data import blank_target, digest, export, split_records, validate_schema, verified_records, write_json
from visual_invoice import FULL_SCHEMA


class TrainingDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "review"
        self.pdfs = self.root / "pdfs"
        for path in (self.workspace / "labels", self.workspace / "images", self.pdfs):
            path.mkdir(parents=True)

    def record(self, name, group, status="verified", page=1):
        source = self.pdfs / f"{name}.pdf"
        source.write_bytes(name.encode())
        image = self.workspace / "images" / f"{name}-{page}.jpg"
        image.write_bytes(f"{name}-{page}".encode())
        target = blank_target()
        target["invoice"] = {"invoice_number": "0001", "payment_method": "CASH"}
        target["other_fields"] = [{"label": "Customer note", "value": "نقدا", "page": page}]
        value = {"pdf": source.name, "source_sha256": digest(source),
                 "page": page, "page_count": 2, "image": f"images/{image.name}",
                 "image_sha256": digest(image), "layout_group": group, "status": status,
                 "reviewer": "checked", "all_visible_fields_checked": True,
                 "rotation_ccw": 0, "unreadable_fields": [], "target": target}
        write_json(self.workspace / "labels" / f"{name}-{page}.json", value)
        return value

    def test_drafts_never_enter_training(self):
        self.record("draft", "seller-a", "draft")
        with self.assertRaisesRegex(ValueError, "No verified labels"):
            export(self.workspace, self.pdfs)

    def test_same_template_pages_stay_together_and_extra_fields_survive(self):
        self.record("a", "seller-a")
        self.record("a", "seller-a", page=2)
        self.record("b", "seller-b")
        self.record("c", "seller-c")
        self.record("unverified", "seller-d", "draft")
        summary = export(self.workspace, self.pdfs)
        self.assertEqual(sum(s["pages"] for s in summary.values()), 4)
        self.assertEqual(sum(s["examples"] for s in summary.values()), 8)
        seen = {}
        for split in summary:
            rows = [json.loads(line) for line in (self.workspace / f"{split}.jsonl").read_text().splitlines()]
            for row in rows:
                self.assertEqual(seen.setdefault(row["layout_group"], split), split)
                if row["scope"] == "header":
                    target = json.loads(row["answer"])
                    self.assertEqual(target["invoice"]["invoice_number"], "0001")
                    self.assertEqual(target["other_fields"][0]["value"], "نقدا")
                    self.assertNotIn("items", target)

    def test_modified_source_is_rejected(self):
        self.record("a", "seller-a")
        (self.pdfs / "a.pdf").write_bytes(b"different PDF")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verified_records(self.workspace, self.pdfs)

    def test_same_pdf_cannot_be_put_in_two_groups(self):
        self.record("a", "seller-a")
        self.record("a", "seller-b", page=2)
        with self.assertRaisesRegex(ValueError, "same layout_group"):
            verified_records(self.workspace, self.pdfs)

    def test_duplicate_page_rejected(self):
        record = self.record("a", "seller-a")
        write_json(self.workspace / "labels" / "duplicate.json", record)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            verified_records(self.workspace, self.pdfs)

    def test_unreadable_labels_not_promoted(self):
        record = self.record("a", "seller-a")
        record["unreadable_fields"] = ["items[0].quantity"]
        write_json(self.workspace / "labels" / "a-1.json", record)
        with self.assertRaisesRegex(ValueError, "unreadable"):
            verified_records(self.workspace, self.pdfs)

    def test_no_schema_silent_drops_or_nonfinite_numbers(self):
        target = blank_target()
        target["invoice"]["unknown_field"] = "value"
        with self.assertRaisesRegex(ValueError, "unknown field"):
            validate_schema(target, FULL_SCHEMA)
        target = blank_target()
        target["totals"]["net_amount"] = float("nan")
        with self.assertRaises(ValueError):
            validate_schema(target, FULL_SCHEMA)
        target["totals"]["net_amount"] = True
        with self.assertRaises(ValueError):
            validate_schema(target, FULL_SCHEMA)

    def test_at_least_three_groups_required(self):
        with self.assertRaisesRegex(ValueError, "at least 3"):
            split_records([self.record("a", "seller-a")])

    def test_draft_and_review_without_setup_report_actionable_error(self):
        notebook = json.loads(Path("colab_train.ipynb").read_text())
        for cell in notebook["cells"]:
            source = "".join(cell["source"])
            if cell["cell_type"] == "code" and ("DRAFT_LIMIT = 1" in source or "from training.review import review" in source):
                with patch("pathlib.Path.is_file", return_value=False):
                    with self.assertRaisesRegex(RuntimeError, "Run Step 1"):
                        exec(compile(source, "cell", "exec"), {})

    def test_partial_draft_warns_without_rewriting_amounts(self):
        from training.draft_checks import draft_warnings
        target = blank_target()
        target["items"] = [{"quantity": 20, "unit_price": 2.35, "amount": 54.05}]
        warnings = draft_warnings(target)
        self.assertTrue(any("invoice:" in warning for warning in warnings))
        self.assertTrue(any("column roles" in warning for warning in warnings))
        self.assertEqual(target["items"][0]["amount"], 54.05)
        # Correct printed gross does not have to equal the pre-tax extension.
        target["items"] = [{"quantity": 20, "unit_price": 2.35, "amount": None,
                            "vat_amount": 7.05, "gross_amount": 54.05}]
        self.assertFalse(any("items[" in warning for warning in draft_warnings(target)))

    def test_generation_stops_only_on_complete_object_or_time_limit(self):
        from training.generation import GenerationMonitor, complete_object
        self.assertIsNone(complete_object('{"invoice":{"date":"2026'))
        self.assertIsNone(complete_object('Explanation {"invoice":{}}'))
        self.assertEqual(complete_object('```json\n{"x":"brace } inside text"}\n```'), '{"x":"brace } inside text"}')
        monitor = GenerationMonitor(120)
        self.assertFalse(monitor.check('{"invoice":', 10, 1))
        self.assertTrue(monitor.check('{"invoice":', 1024, 121))
        self.assertEqual(monitor.reason, "time_limit")
        monitor = GenerationMonitor(120)
        self.assertTrue(monitor.check('{"invoice":{}}', 12, 3))
        self.assertEqual(monitor.reason, "json_complete")

    def test_targeted_draft_skips_verified_pages_before_model_load(self):
        from training.run import draft
        from types import SimpleNamespace
        self.record("a", "seller-a")
        args = SimpleNamespace(workspace=self.workspace, pdf="a.pdf", force=True)
        with patch("training.run.load_runtime") as load:
            draft(args)
        load.assert_not_called()

    def test_generation_timeout_keeps_manual_target_and_records_error(self):
        from training.run import draft
        from types import SimpleNamespace
        from unittest.mock import Mock
        original = self.record("a", "seller-a", status="draft")
        self.record("b", "seller-b", status="draft")
        args = SimpleNamespace(workspace=self.workspace, pdf="a.pdf", force=True,
                               limit=1, max_tokens=4096, header_tokens=1024, item_tokens=2048,
                               generation_seconds=120)
        def generate(model, processor, workspace, row, budget, seconds, diagnostics):
            diagnostics.update(generated_tokens=1024, stop_reason="time_limit")
            return '{"supplier":', 120
        with patch("training.run.load_runtime", return_value=(Mock(), Mock())), \
             patch("training.run.generate", side_effect=generate):
            draft(args)
        result = json.loads((self.workspace / "labels/a-1.json").read_text())
        self.assertEqual(result["target"], original["target"])
        self.assertEqual(len(result["draft_errors"]), 2)
        self.assertEqual(result["suggested_target"], {})
        self.assertNotIn("draft_model", json.loads((self.workspace / "labels/b-1.json").read_text()))

    def test_notebook_python_cells_compile(self):
        notebook = json.loads(Path("colab_train.ipynb").read_text())
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), f"colab_train:cell{index}", "exec")


if __name__ == "__main__":
    unittest.main()
