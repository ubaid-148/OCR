"""Score base/fine-tuned JSON predictions against the untouched test split."""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from training.invoice_dataset import ITEM_KEYS, SECTION_KEYS, normalize_data


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", str(value)).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _equal(expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is actual
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(Decimal(str(expected)) - Decimal(str(actual))) <= Decimal("0.02")
        except (InvalidOperation, ValueError):
            return False
    return _text(expected) == _text(actual)


def flatten(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for section, keys in SECTION_KEYS.items():
        value = data.get(section) if isinstance(data.get(section), dict) else {}
        for key in keys:
            result[f"{section}.{key}"] = value.get(key)
    items = data.get("items") if isinstance(data.get("items"), list) else []
    result["items.row_count"] = len(items)
    for index, item in enumerate(items):
        value = item if isinstance(item, dict) else {}
        for key in ITEM_KEYS:
            result[f"items[{index}].{key}"] = value.get(key)
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def score(manifest_path: Path, predictions_path: Path, split: str = "test") -> dict[str, Any]:
    targets = {row["doc_id"]: row for row in _read_jsonl(manifest_path) if row.get("split") == split}
    predictions = {row["doc_id"]: row for row in _read_jsonl(predictions_path)}
    field_correct = field_total = unexpected = exact_documents = parse_failures = 0
    critical_correct = critical_total = 0
    critical_prefixes = ("invoice.invoice_number", "invoice.date", "supplier.vat_number", "customer.vat_number", "items.row_count", "items[", "totals.")
    details: list[dict[str, Any]] = []
    for doc_id, row in targets.items():
        prediction_row = predictions.get(doc_id, {})
        raw_prediction = prediction_row.get("prediction")
        if isinstance(raw_prediction, str):
            try:
                raw_prediction = json.loads(raw_prediction)
            except json.JSONDecodeError:
                raw_prediction = None
        if not isinstance(raw_prediction, dict):
            parse_failures += 1
            raw_prediction = {}
        expected = flatten(normalize_data(row["target"]))
        actual = flatten(normalize_data(raw_prediction))
        errors: list[str] = []
        for path, expected_value in expected.items():
            actual_value = actual.get(path)
            if expected_value is None:
                unexpected += actual_value is not None
                if actual_value is not None:
                    errors.append(path)
                continue
            field_total += 1
            correct = _equal(expected_value, actual_value)
            field_correct += correct
            if path.startswith(critical_prefixes):
                critical_total += 1
                critical_correct += correct
            if not correct:
                errors.append(path)
        exact_documents += not errors
        details.append({"doc_id": doc_id, "wrong_fields": errors})
    document_count = len(targets)
    return {
        "split": split,
        "documents": document_count,
        "prediction_documents": len(predictions),
        "json_parse_failures": parse_failures,
        "field_accuracy_non_null": round(field_correct / field_total, 6) if field_total else None,
        "critical_accuracy_non_null": round(critical_correct / critical_total, 6) if critical_total else None,
        "exact_document_rate": round(exact_documents / document_count, 6) if document_count else None,
        "unexpected_values_for_null_targets": unexpected,
        "field_correct": field_correct,
        "field_total": field_total,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--split", default="test", choices=("train", "validation", "test"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = score(args.manifest, args.predictions, args.split)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
