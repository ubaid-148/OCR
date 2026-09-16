"""Attribute invoice extraction errors to OCR, layout, vision, or arithmetic.

This is an *offline evidence reader*: it never runs OCR or a model. The PDF is
used to identify the document, while --raw-ocr and --prediction must come from
the same run. An absent token cannot, by itself, distinguish a missed OCR box
from a character error. Such cases are reported as unresolved, not guessed.

Optional ``regions`` in the reference file maps canonical field paths to
{"page": 1, "bbox": [left, top, width, height]}. This is diagnostic metadata,
not part of the application's public result schema. It is needed to prove an
OCR_MISSING or OCR_CHAR_ERROR verdict when the correct token is absent.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any


BUCKETS = (
    "OCR_CHAR_ERROR", "OCR_MISSING", "PARSER_COLUMN", "PARSER_ROW",
    "VISION_OVERRIDE", "ARITHMETIC_INCONSISTENT",
)

ARABIC_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,"
)

ROOT_ALIASES = {"seller": "supplier", "invoice_details": "invoice", "line_items": "items"}
FIELD_ALIASES = {
    "supplier": {"tax_code": "vat_number"},
    "customer": {"tax_code": "vat_number", "name_ar": "name"},
    "invoice": {"invoice_serial": "invoice_number", "invoice_date": "date"},
    "items": {
        "item_id": "item_code", "item_name_ar": "description",
        "taxable_amount": "amount", "tax_amount": "vat_amount",
        "tax_rate": "vat_rate", "tax_code": "tax_code",
        "total_incl_vat": "gross_amount", "price": "unit_price",
    },
    "totals": {
        "total_excluding_vat": "subtotal", "total_vat": "vat_amount",
        "total_amount_including_vat": "net_amount",
        "total_taxable_amount_excluding_vat": "taxable_amount",
    },
}


def normalized(value: Any) -> str:
    """Compare presentation variants without erasing real OCR character errors."""
    if value is None:
        return ""
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return str(Decimal(str(value)).normalize())
    text = unicodedata.normalize("NFKC", str(value)).translate(ARABIC_DIGITS)
    text = " ".join(text.split()).casefold().strip()
    text = text.replace("٪", "%")
    if re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?%?", text):
        suffix = "%" if text.endswith("%") else ""
        try:
            return str(Decimal(text.rstrip("%").replace(",", ".")).normalize()) + suffix
        except InvalidOperation:
            pass
    for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).isoformat()
        except ValueError:
            pass
    return text


def same(left: Any, right: Any) -> bool:
    return normalized(left) == normalized(right)


def _canonical_object(root: str, obj: dict[str, Any]) -> dict[str, Any]:
    aliases = FIELD_ALIASES.get(root, {})
    result: dict[str, Any] = {}
    for key, value in obj.items():
        if key in {"field_evidence", "validation", "amount_source", "_source", "_confidence"}:
            continue
        canonical = aliases.get(key, key)
        if canonical not in result or result[canonical] is None:
            result[canonical] = value
    return result


def canonicalize(document: dict[str, Any]) -> dict[str, Any]:
    data = document.get("data", document)
    if not isinstance(data, dict):
        raise ValueError("Document data must be a JSON object")
    result: dict[str, Any] = {}
    for key, value in data.items():
        root = ROOT_ALIASES.get(key, key)
        if root == "items" and isinstance(value, list):
            result[root] = [_canonical_object(root, row) for row in value if isinstance(row, dict)]
        elif isinstance(value, dict):
            result[root] = _canonical_object(root, value)
        else:
            result[root] = value
    return result


def field_values(data: dict[str, Any]) -> dict[str, Any]:
    """Only reference fields with an asserted (non-null) value are scored."""
    fields: dict[str, Any] = {}
    for root, value in data.items():
        if root == "items" and isinstance(value, list):
            for index, row in enumerate(value):
                for key, item in row.items():
                    if item is not None:
                        fields[f"items[{index}].{key}"] = item
        elif isinstance(value, dict):
            for key, item in value.items():
                if item is not None and not isinstance(item, (list, dict)):
                    fields[f"{root}.{key}"] = item
    return fields


def _lookup(data: dict[str, Any], path: str) -> Any:
    item = re.fullmatch(r"items\[(\d+)\]\.(.+)", path)
    if item:
        index = int(item.group(1))
        rows = data.get("items", [])
        return rows[index].get(item.group(2)) if isinstance(rows, list) and index < len(rows) else None
    root, field = path.split(".", 1)
    obj = data.get(root)
    return obj.get(field) if isinstance(obj, dict) else None


def _bbox(word: dict[str, Any]) -> tuple[float, float, float, float] | None:
    box = word.get("bbox")
    if isinstance(box, dict):
        box = [box.get(k) for k in ("left", "top", "width", "height")]
    if box is None:
        box = [word.get(k) for k in ("left", "top", "width", "height")]
    if not isinstance(box, (list, tuple)) or len(box) != 4 or any(v is None for v in box):
        return None
    try:
        x, y, w, h = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def _overlaps(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    ax, ay, aw, ah = left
    bx, by, bw, bh = right
    return min(ax + aw, bx + bw) > max(ax, bx) and min(ay + ah, by + bh) > max(ay, by)


def ocr_words(raw: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, page in enumerate(raw.get("pages", [])):
        for word in page.get("words", []):
            if not isinstance(word, dict) or not word.get("text"):
                continue
            result.append({"page": page.get("page", index + 1), "text": word["text"],
                           "bbox": _bbox(word), "confidence": word.get("confidence")})
    return result


def _region_words(words: list[dict[str, Any]], region: dict[str, Any]) -> list[dict[str, Any]]:
    box = _bbox(region)
    if box is None:
        raise ValueError("Reference region has no valid bbox")
    return [w for w in words if w["page"] == region.get("page", 1) and
            w["bbox"] is not None and _overlaps(w["bbox"], box)]


def _row_words(path: str, truth: dict[str, Any], words: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Use a unique printed item code to localize a row without format constants."""
    match = re.fullmatch(r"items\[(\d+)\]\..+", path)
    if not match:
        return None
    rows = truth.get("items", [])
    index = int(match.group(1))
    if index >= len(rows) or not rows[index].get("item_code"):
        return None
    code = normalized(rows[index]["item_code"])
    anchors = [word for word in words if normalized(word["text"]) == code and word["bbox"]]
    if len(anchors) != 1:
        return None
    anchor = anchors[0]
    _, y, _, h = anchor["bbox"]
    center_y = y + h / 2
    # Nearby code anchors define row boundaries; otherwise height-scaled band.
    other_centers = []
    for row in rows:
        if row is rows[index] or not row.get("item_code"):
            continue
        matches = [w for w in words if w["page"] == anchor["page"] and
                   normalized(w["text"]) == normalized(row["item_code"]) and w["bbox"]]
        if len(matches) == 1:
            _, other_y, _, other_h = matches[0]["bbox"]
            other_centers.append(other_y + other_h / 2)
    before = max((v for v in other_centers if v < center_y), default=center_y - 4 * h)
    after = min((v for v in other_centers if v > center_y), default=center_y + 4 * h)
    lo, hi = (before + center_y) / 2, (after + center_y) / 2
    return [w for w in words if w["page"] == anchor["page"] and w["bbox"] and
            lo <= w["bbox"][1] + w["bbox"][3] / 2 < hi]


def _evidence_map(prediction: dict[str, Any]) -> dict[str, Any]:
    quality = prediction.get("quality", {})
    result = dict(quality.get("field_evidence", {}) if isinstance(quality, dict) else {})
    rows = prediction.get("data", prediction).get("items", [])
    for index, row in enumerate(rows):
        for key, evidence in row.get("field_evidence", {}).items():
            result[f"items[{index}].{key}"] = evidence
    return result


def _assigned_path(expected: Any, evidence: dict[str, Any]) -> str | None:
    matches = []
    for path, entry in evidence.items():
        entries = entry if isinstance(entry, list) else [entry]
        if any(isinstance(item, dict) and same(item.get("text"), expected) for item in entries):
            matches.append(path)
    return matches[0] if len(matches) == 1 else None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(normalized(value).rstrip("%")) if value is not None else None
    except InvalidOperation:
        return None


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(Decimal("0.02"), abs(b) * Decimal("0.005"))


def _arithmetic_error(path: str, truth: dict[str, Any], pred: dict[str, Any]) -> bool:
    """Arithmetic bucket is reserved for an unsupported computed monetary value.

    Do not flag a printed per-unit taxable/VAT figure as wrong merely because
    quantity exceeds one; both per-unit and line-extended identities are tried.
    """
    match = re.fullmatch(r"items\[(\d+)\]\.(amount|vat_amount|gross_amount)", path)
    if not match:
        return False
    index = int(match.group(1))
    rows = pred.get("items", [])
    if index >= len(rows):
        return False
    row = rows[index]
    q, price, taxable, tax, gross = (_decimal(row.get(k)) for k in
                                      ("quantity", "unit_price", "amount", "vat_amount", "gross_amount"))
    if None in (q, price, taxable, tax, gross):
        return False
    line_base = q * price
    base_candidates = [taxable, taxable * q]
    tax_candidates = [tax, tax * q]
    valid = any(_close(base, line_base - (_decimal(row.get("discount")) or Decimal(0))) and
                _close(gross, base + vat)
                for base in base_candidates for vat in tax_candidates)
    return not valid


def attribute(
    pdf: Path, reference: dict[str, Any], raw: dict[str, Any],
    prediction: dict[str, Any], spatial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise ValueError(f"PDF not found: {pdf}")
    truth = canonicalize(reference)
    pred = canonicalize(prediction)
    spatial_data = canonicalize(spatial) if spatial else None
    words = ocr_words(raw)
    if not words:
        raise ValueError("Raw OCR has no text boxes; supply the Colab raw OCR JSON")
    regions = reference.get("regions", {})
    if not isinstance(regions, dict):
        raise ValueError("regions must be a JSON object")
    evidence = _evidence_map(prediction)
    fields = []
    counts: Counter[str] = Counter()
    unresolved = 0
    for path, expected in field_values(truth).items():
        actual = _lookup(pred, path)
        if same(actual, expected):
            continue
        bucket = None
        reason = ""
        source_words = None
        if spatial_data is not None and same(_lookup(spatial_data, path), expected):
            bucket, reason = "VISION_OVERRIDE", "Spatial value was correct; final value differs"
        else:
            region = regions.get(path)
            if region is not None:
                source_words = _region_words(words, region)
            else:
                source_words = _row_words(path, truth, words)
            if source_words is not None:
                exact = [w for w in source_words if same(w["text"], expected)]
                if exact:
                    assigned = _assigned_path(expected, evidence)
                    row_match = re.fullmatch(r"items\[(\d+)\]\.(.+)", path)
                    assigned_match = re.fullmatch(r"items\[(\d+)\]\.(.+)", assigned or "")
                    if row_match and assigned_match and row_match.group(2) == assigned_match.group(2) and row_match.group(1) != assigned_match.group(1):
                        bucket, reason = "PARSER_ROW", f"Correct source token assigned to {assigned}"
                    else:
                        bucket, reason = "PARSER_COLUMN", "Correct source token is in the expected region but field is wrong or absent"
                elif region is not None:
                    bucket = "OCR_CHAR_ERROR" if source_words else "OCR_MISSING"
                    reason = "Region has OCR boxes but not the expected token" if source_words else "No OCR box overlaps the verified source region"
                else:
                    reason = "Expected item token absent from inferred row; no verified field bbox to separate OCR missing from character error"
            else:
                reason = "No verified source region or unique item-code anchor"

            # A parser row swap can be proved even if the target row's OCR anchor
            # is missing, provided the source evidence records the assignment.
            if bucket is None:
                assigned = _assigned_path(expected, evidence)
                row_match = re.fullmatch(r"items\[(\d+)\]\.(.+)", path)
                assigned_match = re.fullmatch(r"items\[(\d+)\]\.(.+)", assigned or "")
                if row_match and assigned_match and row_match.group(2) == assigned_match.group(2) and row_match.group(1) != assigned_match.group(1):
                    bucket, reason = "PARSER_ROW", f"Correct source token assigned to {assigned}"
            if bucket is None and _arithmetic_error(path, truth, pred) and path not in evidence:
                bucket, reason = "ARITHMETIC_INCONSISTENT", "Computed row value contradicts quantity, price, tax, and total"

        if bucket:
            counts[bucket] += 1
        else:
            unresolved += 1
        fields.append({"field": path, "expected": expected, "actual": actual,
                       "bucket": bucket, "reason": reason})

    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    return {"pdf": str(pdf), "pdf_sha256": digest,
            "status": "complete" if not unresolved else "insufficient_evidence",
            "mismatches": fields,
            "counts": {bucket: counts[bucket] for bucket in BUCKETS},
            "unresolved": unresolved, "compared_fields": len(field_values(truth))}


def report(result: dict[str, Any]) -> str:
    lines = [f"Document: {result['pdf']}",
             f"Attribution: {result['status']} | {len(result['mismatches'])} mismatches | {result['unresolved']} unresolved",
             "Field | Expected | Actual | Bucket", "-" * 100]
    for item in result["mismatches"]:
        lines.append(f"{item['field']} | {item['expected']} | {item['actual']} | {item['bucket'] or 'UNRESOLVED'}")
        lines.append(f"  {item['reason']}")
    lines.append("Counts: " + ", ".join(f"{bucket}={result['counts'][bucket]}" for bucket in BUCKETS))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--raw-ocr", type=Path, required=True)
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--spatial", type=Path, help="Spatial draft, if final prediction used vision")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    try:
        read = lambda path: json.loads(path.read_text(encoding="utf-8"))
        result = attribute(args.pdf, read(args.truth), read(args.raw_ocr),
                           read(args.prediction), read(args.spatial) if args.spatial else None)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Cannot attribute errors: {exc}\n")
    if args.json_out:
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
