"""Score base/fine-tuned JSON predictions against the untouched test split."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from training.invoice_dataset import ITEM_KEYS, SECTION_KEYS, TOP_TEXT_KEYS, normalize_data


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
    for key in TOP_TEXT_KEYS:
        result[key] = data.get(key)
    for key in ("handwritten_notes", "other_fields"):
        values = data.get(key) if isinstance(data.get(key), list) else []
        result[f"{key}.count"] = len(values)
        for index, value in enumerate(values):
            if key == "other_fields":
                field = value if isinstance(value, dict) else {}
                for part in ("label", "value", "page"):
                    result[f"{key}[{index}].{part}"] = field.get(part)
            else:
                result[f"{key}[{index}]"] = value
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
    target_rows = [row for row in _read_jsonl(manifest_path) if row.get("split") == split]
    targets = {row["doc_id"]: row for row in target_rows}
    prediction_rows = _read_jsonl(predictions_path)
    predictions = {row["doc_id"]: row for row in prediction_rows}
    duplicate_ids = sorted({row["doc_id"] for row in prediction_rows if sum(
        other["doc_id"] == row["doc_id"] for other in prediction_rows) > 1})
    missing_ids = sorted(set(targets) - set(predictions))
    extra_ids = sorted(set(predictions) - set(targets))
    field_correct = field_total = unexpected = exact_documents = parse_failures = 0
    critical_correct = critical_total = 0
    item_rows_correct = item_rows_total = 0
    critical_prefixes = ("invoice.invoice_number", "invoice.date", "invoice.date_of_supply",
                         "supplier.name_", "supplier.vat_number", "customer.name", "customer.vat_number",
                         "customer.address", "items.row_count", "items[", "totals.", "vat_summary.")
    details: list[dict[str, Any]] = []
    for doc_id, row in targets.items():
        prediction_row = predictions.get(doc_id, {})
        raw_prediction = prediction_row.get("prediction")
        if isinstance(raw_prediction, str):
            try:
                raw_prediction = json.loads(raw_prediction)
            except json.JSONDecodeError:
                raw_prediction = None
        failed = not isinstance(raw_prediction, dict) or bool(prediction_row.get("error"))
        if failed:
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
        expected_items = normalize_data(row["target"])["items"]
        actual_items = normalize_data(raw_prediction)["items"]
        for index, item in enumerate(expected_items):
            item_rows_total += 1
            actual_item = actual_items[index] if index < len(actual_items) else {}
            item_rows_correct += all(_equal(item.get(key), actual_item.get(key)) for key in ITEM_KEYS)
        exact_documents += not errors and not failed
        details.append({"doc_id": doc_id, "wrong_fields": errors})
    document_count = len(targets)
    return {
        "split": split,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "document_ids": sorted(targets),
        "documents": document_count,
        "prediction_documents": len(predictions),
        "missing_prediction_ids": missing_ids,
        "extra_prediction_ids": extra_ids,
        "duplicate_prediction_ids": duplicate_ids,
        "json_parse_failures": parse_failures,
        "field_accuracy_non_null": round(field_correct / field_total, 6) if field_total else None,
        "critical_accuracy_non_null": round(critical_correct / critical_total, 6) if critical_total else None,
        "exact_document_rate": round(exact_documents / document_count, 6) if document_count else None,
        "item_row_exact_rate": round(item_rows_correct / item_rows_total, 6) if item_rows_total else None,
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
