"""Data correctness tests; no GPU or model download is required."""
import json
from pathlib import Path
import tempfile
import unittest

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

    def test_notebook_python_cells_compile(self):
        notebook = json.loads(Path("colab_train.ipynb").read_text())
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), f"colab_train:cell{index}", "exec")


if __name__ == "__main__":
    unittest.main()
