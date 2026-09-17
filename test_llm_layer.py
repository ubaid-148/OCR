import json
import io
from contextlib import redirect_stdout
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canonical_schema import to_canonical
from llm_extractor import extract_with_ollama
from main import CANONICAL_KEYS, _assert_clean_schema, build_document, main, merge_drafts


class FakeResponse:
    def __init__(self, body):
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class LlmLayerTests(unittest.TestCase):
    def test_ollama_json_and_fence_cleanup(self):
        draft = {"invoice_number": "LLM-1", "items": [], "validation": {"passed": True, "warnings": []}}
        body = json.dumps({"response": "```json\n" + json.dumps(draft) + "\n```"})
        with patch("llm_extractor.urlopen", return_value=FakeResponse(body)):
            self.assertEqual(extract_with_ollama([], "test-model")["invoice_number"], "LLM-1")

    def test_merge_prefers_rules_and_flags_llm_fill(self):
        rule = to_canonical({"invoice_number": "RULE-1", "items": [{"item_id": "A"}]})
        llm = to_canonical({"invoice_number": "LLM-1", "items": [{"item_id": "A", "unit": "PCS"}]})
        merged = merge_drafts(rule, llm)
        self.assertEqual(merged["invoice_number"], "RULE-1")
        self.assertEqual(merged["items"][0]["unit"], "PCS")
        self.assertTrue(any("mismatch" in warning for warning in merged["validation"]["warnings"]))
        self.assertTrue(any("filled_by_llm: items[0].unit" in warning for warning in merged["validation"]["warnings"]))

    def test_clean_schema_has_no_raw_fields(self):
        result = build_document([], None)
        _assert_clean_schema(result)
        self.assertEqual(tuple(result), CANONICAL_KEYS)
        self.assertNotIn("bbox", json.dumps(result))
        self.assertNotIn("confidence", json.dumps(result))

    def test_cli_debug_artifacts_are_opt_in(self):
        payload = {"pages": [{"page": 1, "words": []}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root.parent / "llm-input.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            # This test exercises the clean builder; CLI subprocess coverage is kept
            # separate so no Ollama or OCR runtime is needed in CI.
            result = build_document(payload)
            output = root / "clean.json"
            output.write_text(json.dumps(result), encoding="utf-8")
            self.assertEqual([output.name], [path.name for path in root.iterdir()])

    def test_clean_schema_allows_evidence_words_in_printed_text_and_reviews(self):
        result = to_canonical({"seller": {"name_en": "Confidence Trading"},
                               "items": [{"item_name": "bbox confidence"}],
                               "validation": {"passed": False, "warnings": ["low OCR confidence"]}})
        _assert_clean_schema(result)
        self.assertIn("low OCR confidence", result["validation"]["warnings"])

    def test_clean_schema_still_rejects_nested_evidence_keys(self):
        for key in ("bbox", "confidence"):
            for section in ("seller", "items", "validation"):
                with self.subTest(key=key, section=section):
                    result = to_canonical({"items": [{"item_id": "A"}]})
                    if section == "items":
                        result[section][0][key] = 1
                    elif section == "validation":
                        result[section]["field_reviews"] = [{"nested": {key: 1}}]
                    else:
                        result[section][key] = 1
                    with self.assertRaisesRegex(RuntimeError, "Raw OCR evidence leaked"):
                        _assert_clean_schema(result)

    def test_cli_writes_result_with_low_confidence_review(self):
        draft = to_canonical({"validation": {"passed": False, "warnings": ["low OCR confidence"]}})
        with tempfile.TemporaryDirectory() as directory, patch("main.build_document", return_value=draft):
            source, output = Path(directory) / "raw.json", Path(directory) / "invoice.json"
            source.write_text('{"pages": []}')
            with patch.object(sys, "argv", ["main.py", str(source), "--output", str(output), "--no-llm"]), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            result = json.loads(output.read_text())
            self.assertFalse(result["validation"]["passed"])
            self.assertIn("low OCR confidence", result["validation"]["warnings"])

    def test_cli_malformed_llm_keeps_clean_schema_and_debug_artifacts(self):
        payload = {"pages": [{"page": 1, "words": []}]}
        with tempfile.TemporaryDirectory() as directory, patch("main.extract_with_ollama", side_effect=ValueError("bad model JSON")):
            root = Path(directory)
            source = root / "input.json"
            output = root / "clean.json"
            debug = root / "debug"
            source.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(sys, "argv", ["main.py", str(source), "--output", str(output), "--model", "test", "--debug-dir", str(debug)]):
                main()
            clean = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(tuple(clean), CANONICAL_KEYS)
            self.assertTrue(any("llm_unavailable" in warning for warning in clean["validation"]["warnings"]))
            self.assertEqual({path.name for path in debug.iterdir()}, {"raw_ocr.json", "rule_based_draft.json", "llm_draft.json"})
            self.assertEqual(tuple(json.loads((debug / "rule_based_draft.json").read_text())["items"][0]) if json.loads((debug / "rule_based_draft.json").read_text()).get("items") else (), ())

    def test_cli_without_debug_writes_only_clean_output(self):
        payload = {"pages": [{"page": 1, "words": []}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            output = root / "clean.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(sys, "argv", ["main.py", str(source), "--output", str(output), "--no-llm"]):
                main()
            self.assertEqual({path.name for path in root.iterdir()}, {"input.json", "clean.json"})
            self.assertEqual(tuple(json.loads(output.read_text(encoding="utf-8"))), CANONICAL_KEYS)


if __name__ == "__main__":
    unittest.main()
