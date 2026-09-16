import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

from training.compare_metrics import compare
from training.adapter_service import adapter_digest, file_digest, load_approval
from training.github_release import bundle_adapter, download_bundle
from training.github_sync import push_verified_labels
from training.invoice_dataset import (
    ANNOTATION_VERSION,
    empty_data,
    export_qwen,
    new_annotation,
    normalize_data,
    split_entries,
    validate_annotation,
    validation_report,
)
from training.register_qwen_dataset import register
from training.patch_qwen_single_gpu import patch
from training.run_vlm_eval import _parse_json
from training.score_predictions import score


def complete_data(number="INV-1", supplier="300000000000001"):
    data = empty_data()
    data["supplier"].update(name_ar="شركة تجريبية", vat_number=supplier)
    data["invoice"].update(invoice_number=number, date="2026-01-02")
    data["customer"].update(name="عميل", vat_number="300000000000099")
    data["items"] = [{
        "line_no": 1, "item_code": "A-1", "description": "منتج", "quantity": 2,
        "unit": "PCS", "unit_price": 10, "amount": 20, "vat_amount": 3,
        "discount": 0, "gross_amount": 23,
    }]
    data["totals"].update(subtotal=20, discount=0, vat_rate=15, vat_amount=3, net_amount=23, currency="SAR")
    return normalize_data(data)


class TrainingDatasetTests(unittest.TestCase):
    def test_github_push_refuses_unverified_edit_of_published_label(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "OCR"
            work = root / "work"
            labels = project / "public_invoice_labels"
            labels.mkdir(parents=True)
            work.mkdir()
            annotation = new_annotation("one.pdf", "sha", complete_data("one", "3000"))
            (labels / "one.json").write_text(json.dumps(annotation), encoding="utf-8")
            (work / "manifest.json").write_text(json.dumps({
                "manifest_version": "invoice-ocr-private-workspace-v1",
                "labels_root": str(labels),
                "documents": [{"doc_id": "one", "source_filename": "one.pdf", "source_sha256": "sha"}],
            }), encoding="utf-8")
            git_outputs = [str(project.resolve()), "https://github.com/ubaid-148/OCR.git", "",
                           "public_invoice_labels/one.json"]
            with mock_patch("training.github_sync._git", side_effect=git_outputs):
                with self.assertRaisesRegex(ValueError, "not re-verified"):
                    push_verified_labels(project, work, "dummy-token")

    def test_normalizes_cloud_style_output_without_template_values(self):
        data = normalize_data({
            "seller": {"name_ar": "بائع", "tax_code": "300123"},
            "invoice_details": {"invoice_serial": "X-7", "invoice_date": "2026-02-03T04:05:06", "date_of_supply": "2026-02-04"},
            "customer": {"name_ar": "مشتري", "tax_code": "300456"},
            "line_items": [{"item_id": "P 1", "item_name_ar": "وصف", "quantity": 1, "taxable_amount": 8, "tax_amount": 1.2, "total_incl_vat": 9.2}],
            "totals": {"total_excluding_vat": 8, "total_vat": 1.2, "total_amount_including_vat": 9.2, "currency": "SAR"},
        })
        self.assertEqual(data["supplier"]["vat_number"], "300123")
        self.assertEqual(data["invoice"]["invoice_number"], "X-7")
        self.assertEqual(data["invoice"]["date"], "2026-02-03")
        self.assertEqual(data["invoice"]["time"], "04:05:06")
        self.assertEqual(data["items"][0]["item_code"], "P 1")
        self.assertEqual(data["totals"]["net_amount"], 9.2)

    def test_validator_flags_shape_but_preserves_printed_arithmetic_warning(self):
        annotation = new_annotation("sample.pdf", "abc", complete_data())
        annotation.update(verified=True, verified_by="reviewer", verified_at="now", include_in_training=True)
        annotation["data"]["totals"]["net_amount"] = 99
        errors, warnings = validate_annotation(annotation, require_verified=True)
        self.assertEqual(errors, [])
        self.assertTrue(any("do not reconcile" in value for value in warnings))

    def test_split_never_leaks_supplier_group(self):
        entries = []
        for index in range(12):
            group = index // 3
            annotation = new_annotation(f"{index}.pdf", str(index), complete_data(str(index), f"300{group}"))
            entries.append({"doc_id": str(index), "source_sha256": str(index), "annotation": annotation, "pages": [f"{index}.png"]})
        splits = split_entries(entries)
        seen = {}
        for split, members in splits.items():
            for member in members:
                if member["group"] in seen:
                    self.assertEqual(seen[member["group"]], split)
                seen[member["group"]] = split
        self.assertTrue(all(splits.values()))

    def test_private_export_and_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("images", "drafts", "public_labels", "exports"):
                (root / name).mkdir()
            records = []
            for index in range(9):
                doc_id = f"doc-{index}"
                image = root / "images" / f"{doc_id}.png"
                image.write_bytes(b"image")
                pdf = root / f"{index}.pdf"
                pdf.write_bytes(f"pdf-{index}".encode())
                from training.invoice_dataset import _sha256
                record = {"doc_id": doc_id, "source_filename": pdf.name, "source_path": str(pdf), "source_sha256": _sha256(pdf), "pages": [str(image)]}
                records.append(record)
                annotation = new_annotation(record["source_filename"], record["source_sha256"], complete_data(str(index), f"300{index // 3}"))
                annotation.update(verified=True, include_in_training=True, verified_by="reviewer", verified_at="now")
                (root / "public_labels" / f"{doc_id}.json").write_text(json.dumps(annotation, ensure_ascii=False), encoding="utf-8")
            (root / "manifest.json").write_text(json.dumps({"manifest_version": "invoice-ocr-private-workspace-v1",
                                                            "labels_root": str(root / "public_labels"), "documents": records}), encoding="utf-8")
            report = validation_report(root)
            self.assertEqual(report["included"], 9)
            summary = export_qwen(root, min_verified=3)
            self.assertEqual(sum(summary["counts"].values()), 9)
            exported = json.loads((root / "exports" / "qwen_train.json").read_text(encoding="utf-8"))
            self.assertEqual(exported[0]["conversations"][0]["value"].count("<image>"), 1)
            manifest_rows = [json.loads(line) for line in (root / "exports" / "evaluation_manifest.jsonl").read_text(encoding="utf-8").splitlines()]
            test_rows = [row for row in manifest_rows if row["split"] == "test"]
            predictions = root / "predictions.jsonl"
            predictions.write_text("".join(json.dumps({"doc_id": row["doc_id"], "prediction": row["target"]}, ensure_ascii=False) + "\n" for row in test_rows), encoding="utf-8")
            metrics = score(root / "exports" / "evaluation_manifest.jsonl", predictions)
            self.assertEqual(metrics["exact_document_rate"], 1.0)
            self.assertEqual(metrics["critical_accuracy_non_null"], 1.0)
            self.assertEqual(metrics["item_row_exact_rate"], 1.0)

    def test_full_cloud_schema_is_preserved_and_v1_requires_reverification(self):
        data = normalize_data({
            "document_type": "Tax Invoice", "handwritten_notes": ["9498"],
            "seller": {"name_en": "Trading Co.", "branch": "Thuqbah", "cr_number": "2050209041", "building_no": "6595"},
            "invoice_details": {"invoice_serial": "2690111862", "ref_no": "REF-1", "page": "1 of 1"},
            "customer": {"cus_code": "104551", "name_ar": "Buyer", "building_no": "3518", "short_address": "EEDA3518"},
            "line_items": [{"item_id": "1212", "item_name_ar": "HELIX 15/40", "tax_rate": "15%", "tax_code": "S", "quantity": 2}],
            "vat_summary": {"before_tax": 94.81, "tax_amount": 14.22, "inc_tax": 109.03, "tax_code": "S"},
            "totals": {"total_excluding_vat": 94.81, "other_charges": 0,
                       "total_taxable_amount_excluding_vat": 94.81, "total_amount_including_vat": 109.03},
        })
        self.assertEqual(data["supplier"]["building_no"], "6595")
        self.assertEqual(data["customer"]["building_no"], "3518")
        self.assertIsNone(data["customer"]["address"])
        self.assertEqual(data["items"][0]["tax_rate"], 15)
        self.assertEqual(data["items"][0]["description_ar"], "HELIX 15/40")
        self.assertEqual(data["vat_summary"]["inc_tax"], 109.03)
        self.assertEqual(data["totals"]["taxable_amount"], 94.81)
        old = new_annotation("9498.pdf", "hash", data)
        old["annotation_version"] = "invoice-ocr-annotation-v1"
        errors, _ = validate_annotation(old)
        self.assertTrue(any("annotation_version" in error for error in errors))
        self.assertEqual(ANNOTATION_VERSION, "invoice-ocr-annotation-v2")

    def test_layout_and_supplier_are_both_isolated(self):
        entries = []
        for index, (vat, layout) in enumerate((("300000000000001", "x"), ("300000000000001", "y"),
                                               ("300000000000002", "y"), ("300000000000003", "z"),
                                               ("300000000000004", "q"))):
            label = new_annotation(f"{index}.pdf", f"hash-{index}", complete_data(supplier=vat))
            label["layout_group"] = layout
            entries.append({"source_sha256": f"hash-{index}", "annotation": label})
        splits = split_entries(entries)
        locations = {entry["source_sha256"]: split for split, members in splits.items() for entry in members}
        self.assertEqual(locations["hash-0"], locations["hash-1"])
        self.assertEqual(locations["hash-1"], locations["hash-2"])

    def test_qwen_registration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "qwenvl" / "data"
            registry.mkdir(parents=True)
            target = registry / "__init__.py"
            target.write_text("data_dict = {\n    'other': {},\n}\n", encoding="utf-8")
            train, validation = root / "train.json", root / "validation.json"
            train.write_text("[]", encoding="utf-8")
            validation.write_text("[]", encoding="utf-8")
            register(root, train, validation)
            register(root, train, validation)
            value = target.read_text(encoding="utf-8")
            self.assertEqual(value.count("BEGIN PRIVATE_INVOICE_DATASET"), 1)
            self.assertEqual(value.count("'private_invoice_train'"), 1)

    def test_approval_rejects_changed_adapter_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = root / "adapter"
            adapter.mkdir()
            (adapter / "adapter_config.json").write_text(
                json.dumps({"base_model_name_or_path": "Qwen/Qwen3-VL-2B-Instruct"}), encoding="utf-8")
            weights = adapter / "adapter_model.safetensors"
            weights.write_bytes(b"initial weights")
            metrics = {"split": "test", "documents": 10, "prediction_documents": 10,
                       "manifest_sha256": "manifest", "document_ids": [str(i) for i in range(10)],
                       "json_parse_failures": 0, "critical_accuracy_non_null": 1.0,
                       "field_accuracy_non_null": 1.0, "exact_document_rate": 1.0,
                       "item_row_exact_rate": 1.0, "unexpected_values_for_null_targets": 0,
                       "missing_prediction_ids": [], "extra_prediction_ids": [], "duplicate_prediction_ids": []}
            for name in ("base.json", "candidate.json"):
                (root / name).write_text(json.dumps(metrics), encoding="utf-8")
            approval = {"approval_version": "invoice-adapter-approval-v1", "approved": True,
                        "prompt_version": "invoice-json-full-v2", "adapter_dir": str(adapter),
                        "model_id": "Qwen/Qwen3-VL-2B-Instruct",
                        "adapter_sha256": adapter_digest(adapter), "base_test_metrics": str(root / "base.json"),
                        "adapter_test_metrics": str(root / "candidate.json"),
                        "base_test_sha256": file_digest(root / "base.json"),
                        "adapter_test_sha256": file_digest(root / "candidate.json")}
            approval_path = root / "approval.json"
            approval_path.write_text(json.dumps(approval), encoding="utf-8")
            self.assertEqual(load_approval(approval_path)["adapter_dir"], str(adapter))
            archive, tag = bundle_adapter(approval_path, root / "bundle.zip")
            release = {"assets": [{"name": "invoice-adapter-v2.zip", "browser_download_url": "https://example.test/bundle.zip",
                                   "digest": f"sha256:{file_digest(archive)}"}]}
            with mock_patch("training.github_release._request", return_value=release), \
                 mock_patch("training.github_release.urllib.request.urlopen", return_value=io.BytesIO(archive.read_bytes())):
                portable = download_bundle(tag, root / "downloaded")
            self.assertEqual(load_approval(portable)["model_id"], "Qwen/Qwen3-VL-2B-Instruct")
            weights.write_bytes(b"different weights")
            with self.assertRaisesRegex(ValueError, "changed"):
                load_approval(approval_path)

    def test_quality_gate_rejects_regression(self):
        base = {"field_accuracy_non_null": .9, "critical_accuracy_non_null": .9, "exact_document_rate": .8, "unexpected_values_for_null_targets": 1}
        candidate = {"field_accuracy_non_null": .95, "critical_accuracy_non_null": .89, "exact_document_rate": .85, "unexpected_values_for_null_targets": 1, "json_parse_failures": 0}
        self.assertTrue(compare(base, candidate, .85, .8))

    def test_json_parser_rejects_trailing_commentary(self):
        with self.assertRaises(json.JSONDecodeError):
            _parse_json('{"supplier":{}} and here is an explanation')

    def test_gate_rejects_wrong_item_rows_even_with_good_header(self):
        base = {"split": "test", "documents": 10, "prediction_documents": 10,
                "manifest_sha256": "same", "document_ids": [str(i) for i in range(10)],
                "json_parse_failures": 0, "critical_accuracy_non_null": 0.99,
                "field_accuracy_non_null": 0.99, "exact_document_rate": 0.90,
                "item_row_exact_rate": 0.99, "unexpected_values_for_null_targets": 0}
        candidate = dict(base, item_row_exact_rate=0.80)
        self.assertTrue(any("item-row" in message for message in compare(base, candidate, .98, .90)))

    def test_prepare_refuses_private_data_inside_git_worktree(self):
        from training.invoice_dataset import prepare_workspace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            pdfs = root / "pdfs"
            pdfs.mkdir()
            (pdfs / "one.pdf").write_bytes(b"not opened because privacy guard runs first")
            with self.assertRaisesRegex(ValueError, "WORK_DIR must stay outside"):
                prepare_workspace(pdfs, root / "private")

    def test_qwen_colab_patch_is_checked_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "qwenvl" / "train"
            entry.mkdir(parents=True)
            target = entry / "train_qwen.py"
            target.write_text(
                "from trainer import replace_qwen2_vl_attention_class\n"
                "x = dtype=(torch.bfloat16 if training_args.bf16 else None),\n"
                "train(attn_implementation=\"flash_attention_2\")\n",
                encoding="utf-8",
            )
            patch(root)
            patch(root)
            value = target.read_text(encoding="utf-8")
            self.assertEqual(value.count("OCR_COLAB_SDPA_PATCH_V1"), 1)
            self.assertIn('train(attn_implementation="sdpa")', value)
            self.assertIn("training_args.fp16", value)


if __name__ == "__main__":
    unittest.main()
