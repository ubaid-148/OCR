"""Prepare, validate and export a private multi-layout invoice dataset.

PDFs, page images, annotations and model weights belong in a private work
directory. Nothing in this module uploads them or copies them into the repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from visual_invoice import ITEM_NUMBERS, ITEM_TEXT, TEXT_FIELDS, TOTAL_NUMBERS, VAT_NUMBERS


ANNOTATION_VERSION = "invoice-ocr-annotation-v2"
PROMPT_VERSION = "invoice-json-full-v2"
PROMPT = """Extract every printed invoice field from all supplied page images into one JSON object.
The document may use any Arabic, English, or bilingual layout. Read labels and spatial relationships; do not assume a supplier template.
Return document_type, document_type_ar, handwritten_notes, amount_in_words_ar, supplier, invoice, customer, items, vat_summary, totals and other_fields.
supplier keys: name_ar, name_en, branch, vat_number, cr_number, building_no, street, area, post_code, additional_no, short_address, country, city.
invoice keys: invoice_number, date, date_of_supply, hijri_date, time, ref_no, payment_method, page.
customer keys: customer_code, name, name_ar, name_en, vat_number, cr_number, building_no, street, area, post_code, additional_no, short_address, country, city, address.
Each items entry keys: line_no, item_code, description, description_ar, description_en, unit, tax_code, quantity, unit_price, discount, amount, tax_rate, vat_amount, gross_amount.
vat_summary keys: before_tax, tax_amount, inc_tax, tax_code.
totals keys: subtotal, discount, other_charges, taxable_amount, vat_rate, vat_amount, net_amount, currency.
other_fields is an array of objects with label, value and one-based page number.
Use JSON null when a value is not printed or genuinely unreadable; use [] for absent notes/other fields. Preserve leading zeros, source spelling, line order and seller/buyer roles. Never calculate a missing source value. amount is the printed pre-tax line amount; gross_amount is the printed line total including VAT. Return JSON only."""

SECTION_KEYS = {
    **TEXT_FIELDS,
    "vat_summary": (*VAT_NUMBERS, "tax_code"),
    "totals": (*TOTAL_NUMBERS, "currency"),
}
ITEM_KEYS = ("line_no", *ITEM_TEXT, *ITEM_NUMBERS)
NUMERIC_ITEM_KEYS = set(ITEM_NUMBERS)
NUMERIC_SECTION_KEYS = {"vat_summary": set(VAT_NUMBERS), "totals": set(TOTAL_NUMBERS)}
TOP_TEXT_KEYS = ("document_type", "document_type_ar", "amount_in_words_ar")
TOP_ARRAY_KEYS = ("handwritten_notes", "other_fields")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def empty_data() -> dict[str, Any]:
    return {
        **{key: None for key in TOP_TEXT_KEYS},
        "handwritten_notes": [],
        **{section: {key: None for key in keys} for section, keys in SECTION_KEYS.items()},
        "items": [],
        "other_fields": [],
    }


def _numeric(value: Any, percent: bool = False) -> Any:
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if percent:
            cleaned = cleaned.removesuffix("%").strip()
        try:
            number = Decimal(cleaned)
            return float(number) if number.is_finite() else value
        except InvalidOperation:
            return value  # The validator will flag unreadable numeric labels.
    return value


def normalize_data(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Map app/debug/cloud-style output into the full training schema."""
    payload = payload or {}
    source = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    seller = source.get("supplier") or source.get("seller") or {}
    invoice = source.get("invoice") or source.get("invoice_details") or {}
    customer = source.get("customer") or {}
    totals = source.get("totals") or {}
    vat = source.get("vat_summary") or {}
    raw_items = source.get("items") or source.get("line_items") or []

    result = empty_data()
    for key in TOP_TEXT_KEYS:
        result[key] = source.get(key)
    for key in ("handwritten_notes", "other_fields"):
        result[key] = source.get(key) if isinstance(source.get(key), list) else []
    for section, raw in (("supplier", seller), ("invoice", invoice), ("customer", customer),
                         ("vat_summary", vat), ("totals", totals)):
        if isinstance(raw, dict):
            result[section].update({key: raw.get(key) for key in SECTION_KEYS[section]})
    result["supplier"]["vat_number"] = _pick(seller, "vat_number", "tax_code")
    result["invoice"]["invoice_number"] = _pick(invoice, "invoice_number", "invoice_serial")
    result["invoice"]["date"] = _pick(invoice, "date", "invoice_date")
    result["invoice"]["date_of_supply"] = _pick(invoice, "date_of_supply", "supply_date")
    if isinstance(result["invoice"]["date"], str) and "T" in result["invoice"]["date"]:
        date, time = result["invoice"]["date"].split("T", 1)
        result["invoice"]["date"] = date
        result["invoice"]["time"] = result["invoice"]["time"] or time
    result["customer"]["customer_code"] = _pick(customer, "customer_code", "cus_code")
    result["customer"]["name"] = _pick(customer, "name", "name_ar", "name_en")
    result["customer"]["vat_number"] = _pick(customer, "vat_number", "tax_code")
    for index, item in enumerate(raw_items if isinstance(raw_items, list) else []):
        if not isinstance(item, dict):
            continue
        row = {key: item.get(key) for key in ITEM_KEYS}
        row["line_no"] = item.get("line_no", index + 1)
        row["item_code"] = _pick(item, "item_code", "item_id")
        row["description"] = _pick(item, "description", "item_name_ar", "item_name_en")
        row["description_ar"] = _pick(item, "description_ar", "item_name_ar")
        row["description_en"] = _pick(item, "description_en", "item_name_en")
        row["amount"] = _pick(item, "amount", "taxable_amount")
        row["vat_amount"] = _pick(item, "vat_amount", "tax_amount")
        row["gross_amount"] = _pick(item, "gross_amount", "total_incl_vat")
        for key in NUMERIC_ITEM_KEYS:
            row[key] = _numeric(row[key], key == "tax_rate")
        result["items"].append(row)
    result["vat_summary"]["before_tax"] = _numeric(_pick(vat, "before_tax"))
    result["vat_summary"]["tax_amount"] = _numeric(_pick(vat, "tax_amount"))
    result["vat_summary"]["inc_tax"] = _numeric(_pick(vat, "inc_tax"))
    result["totals"]["subtotal"] = _pick(totals, "subtotal", "total_excluding_vat")
    result["totals"]["taxable_amount"] = _pick(totals, "taxable_amount", "total_taxable_amount_excluding_vat")
    result["totals"]["vat_amount"] = _pick(totals, "vat_amount", "total_vat")
    result["totals"]["net_amount"] = _pick(totals, "net_amount", "total_amount_including_vat")
    for key in NUMERIC_SECTION_KEYS["totals"]:
        result["totals"][key] = _numeric(result["totals"][key], key == "vat_rate")
    return result


def new_annotation(filename: str, source_sha256: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "annotation_version": ANNOTATION_VERSION,
        "source_filename": filename,
        "source_sha256": source_sha256,
        "verified": False,
        "include_in_training": False,
        "verified_by": None,
        "verified_at": None,
        "layout_group": None,
        "notes": None,
        "data": normalize_data(data),
    }


def _is_number(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def validate_annotation(annotation: dict[str, Any], require_verified: bool = False) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if annotation.get("annotation_version") != ANNOTATION_VERSION:
        errors.append(f"annotation_version must be {ANNOTATION_VERSION}")
    for key in ("source_filename", "source_sha256"):
        if not isinstance(annotation.get(key), str) or not annotation[key].strip():
            errors.append(f"{key} must be a non-empty string")
    if require_verified and annotation.get("verified") is not True:
        errors.append("annotation is not verified")
    if annotation.get("verified") is True and not str(annotation.get("verified_by") or "").strip():
        errors.append("verified_by is required when verified=true")
    if annotation.get("verified") is True and not str(annotation.get("verified_at") or "").strip():
        errors.append("verified_at is required when verified=true")
    data = annotation.get("data")
    if not isinstance(data, dict):
        return errors + ["data must be an object"], warnings
    allowed = set(SECTION_KEYS) | set(TOP_TEXT_KEYS) | set(TOP_ARRAY_KEYS) | {"items"}
    unknown_top = sorted(set(data) - allowed)
    if unknown_top:
        errors.append(f"data has unsupported keys: {', '.join(unknown_top)}")
    for key in TOP_TEXT_KEYS:
        if key not in data:
            errors.append(f"data.{key} is missing")
        elif data[key] is not None and not isinstance(data[key], str):
            errors.append(f"data.{key} must be a string or null")
    notes = data.get("handwritten_notes")
    if not isinstance(notes, list) or any(not isinstance(value, str) for value in notes):
        errors.append("data.handwritten_notes must be an array of strings")
    other = data.get("other_fields")
    if not isinstance(other, list):
        errors.append("data.other_fields must be an array")
    else:
        for index, field in enumerate(other):
            if (not isinstance(field, dict) or set(field) != {"label", "value", "page"}
                    or any(not isinstance(field.get(key), str) or not field[key].strip() for key in ("label", "value"))
                    or not isinstance(field.get("page"), int) or isinstance(field.get("page"), bool)
                    or field["page"] < 1):
                errors.append(f"data.other_fields[{index}] must have non-empty label/value and positive integer page")
    for section, keys in SECTION_KEYS.items():
        value = data.get(section)
        if not isinstance(value, dict):
            errors.append(f"data.{section} must be an object")
            continue
        unknown = sorted(set(value) - set(keys))
        if unknown:
            errors.append(f"data.{section} has unsupported keys: {', '.join(unknown)}")
        for key in keys:
            if key not in value:
                errors.append(f"data.{section}.{key} is missing")
            elif key in NUMERIC_SECTION_KEYS.get(section, set()) and not _is_number(value[key]):
                errors.append(f"data.{section}.{key} must be a number or null")
            elif key not in NUMERIC_SECTION_KEYS.get(section, set()) and value[key] is not None and not isinstance(value[key], str):
                errors.append(f"data.{section}.{key} must be a string or null")
    items = data.get("items")
    if not isinstance(items, list):
        errors.append("data.items must be an array")
        return errors, warnings
    for index, item in enumerate(items):
        path = f"data.items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path} must be an object")
            continue
        unknown = sorted(set(item) - set(ITEM_KEYS))
        if unknown:
            errors.append(f"{path} has unsupported keys: {', '.join(unknown)}")
        for key in ITEM_KEYS:
            if key not in item:
                errors.append(f"{path}.{key} is missing")
            elif key == "line_no" and (not isinstance(item[key], int) or isinstance(item[key], bool) or item[key] < 1):
                errors.append(f"{path}.line_no must be a positive integer")
            elif key in NUMERIC_ITEM_KEYS and not _is_number(item[key]):
                errors.append(f"{path}.{key} must be a number or null")
            elif key not in NUMERIC_ITEM_KEYS | {"line_no"} and item[key] is not None and not isinstance(item[key], str):
                errors.append(f"{path}.{key} must be a string or null")

    if not items:
        warnings.append("invoice has no item rows")
    if annotation.get("include_in_training") is True and annotation.get("verified") is not True:
        errors.append("include_in_training=true requires verified=true")
    invoice = data.get("invoice") if isinstance(data.get("invoice"), dict) else {}
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    for field in ("invoice_number", "date"):
        if invoice.get(field) is None:
            warnings.append(f"invoice.{field} is null; confirm it is absent/unreadable in the PDF")
    for field in ("subtotal", "vat_amount", "net_amount"):
        if totals.get(field) is None:
            warnings.append(f"totals.{field} is null; confirm it is absent/unreadable in the PDF")
    subtotal, discount, vat, net = (_decimal(totals.get(key)) for key in ("subtotal", "discount", "vat_amount", "net_amount"))
    if subtotal is not None and vat is not None and net is not None:
        expected = subtotal - (discount or Decimal("0")) + vat
        if abs(expected - net) > Decimal("0.02"):
            warnings.append("printed totals do not reconcile within 0.02; verify source values without replacing them")
    if items and subtotal is not None:
        amounts = [_decimal(item.get("amount")) for item in items if isinstance(item, dict)]
        if all(value is not None for value in amounts) and abs(sum(amounts, Decimal("0")) - subtotal) > Decimal("0.02"):
            warnings.append("sum of printed item amounts differs from subtotal; verify source columns")
    supplier = data.get("supplier") if isinstance(data.get("supplier"), dict) else {}
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    if supplier.get("vat_number") and supplier.get("vat_number") == customer.get("vat_number"):
        warnings.append("supplier and customer VAT numbers are identical; verify their roles")
    return errors, warnings


def _pdf_paths(pdf_dir: Path) -> list[Path]:
    paths = sorted(pdf_dir.rglob("*.pdf"), key=lambda path: (path.name.casefold(), str(path).casefold()))
    if not paths:
        raise ValueError(f"No PDF files found under {pdf_dir}")
    return paths


def _inside_git_worktree(path: Path) -> bool:
    path = path.resolve()
    return any((candidate / ".git").exists() for candidate in (path, *path.parents))


def _inside_project_tree(path: Path) -> bool:
    root = Path(__file__).resolve().parents[1]
    path = path.resolve()
    return path == root or root in path.parents


def _safe_id(path: Path, digest: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.stem).strip("-.") or "invoice"
    result = base
    if result.casefold() in used:
        result = f"{base}-{digest[:10]}"
    used.add(result.casefold())
    return result


def prepare_workspace(
    pdf_dir: Path, work_dir: Path, dpi: int = 200, allow_public_pdf_dir: bool = False,
    labels_dir: Path | None = None, allow_public_labels: bool = False,
) -> dict[str, Any]:
    try:
        import pypdfium2 as pdfium
    except ImportError as error:
        raise RuntimeError("pypdfium2 is required: pip install pypdfium2") from error
    pdf_dir, work_dir = pdf_dir.resolve(), work_dir.resolve()
    labels_dir = labels_dir.resolve() if labels_dir is not None else work_dir / "labels"
    if _inside_git_worktree(work_dir) or _inside_project_tree(work_dir):
        raise ValueError("WORK_DIR must stay outside a Git worktree to prevent labels and weights entering public commits")
    if (_inside_git_worktree(pdf_dir) or _inside_project_tree(pdf_dir)) and not allow_public_pdf_dir:
        raise ValueError("PDF_DIR is inside a Git worktree; pass --allow-public-pdf-dir only for intentionally public PDFs")
    if (_inside_git_worktree(labels_dir) or _inside_project_tree(labels_dir)) and not allow_public_labels:
        raise ValueError("LABELS_DIR is in a public Git worktree; pass --allow-public-labels only when public labels are authorized")
    work_dir.mkdir(parents=True, exist_ok=True)
    for name in ("images", "drafts", "exports", "failures"):
        (work_dir / name).mkdir(exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    used: set[str] = set()
    for path in _pdf_paths(pdf_dir):
        digest = _sha256(path)
        doc_id = _safe_id(path, digest, used)
        image_dir = work_dir / "images" / doc_id
        image_dir.mkdir(exist_ok=True)
        page_paths: list[str] = []
        with pdfium.PdfDocument(str(path)) as document:
            for index in range(len(document)):
                page = document[index]
                output = image_dir / f"page-{index + 1:03d}.png"
                try:
                    if not output.exists():
                        bitmap = page.render(scale=dpi / 72)
                        try:
                            bitmap.to_pil().convert("RGB").save(output, optimize=True)
                        finally:
                            bitmap.close()
                finally:
                    page.close()
                page_paths.append(str(output.resolve()))
        record = {
            "doc_id": doc_id,
            "source_filename": path.name,
            "source_path": str(path),
            "source_sha256": digest,
            "pages": page_paths,
        }
        records.append(record)
        draft_path = work_dir / "drafts" / f"{doc_id}.json"
        if not draft_path.exists():
            draft_path.write_text(json.dumps(new_annotation(path.name, digest), ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "manifest_version": "invoice-ocr-private-workspace-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pdf_root": str(pdf_dir),
        "labels_root": str(labels_dir),
        "dpi": dpi,
        "documents": records,
    }
    (work_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def load_manifest(work_dir: Path) -> dict[str, Any]:
    path = work_dir.resolve() / "manifest.json"
    if not path.exists():
        raise ValueError(f"Missing {path}; run prepare first")
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("manifest_version") != "invoice-ocr-private-workspace-v1":
        raise ValueError("Unsupported private workspace manifest")
    result.setdefault("labels_root", str(work_dir.resolve() / "labels"))
    return result


def draft_workspace(work_dir: Path, languages: str = "ara+eng", overwrite: bool = False) -> dict[str, int]:
    """Run the existing OCR in Colab to make editable drafts; never marks them verified."""
    from coordinate_ocr import extract_pdf
    from local_ai_parser import parse_invoice_hybrid

    manifest = load_manifest(work_dir)
    completed = skipped = failed = 0
    for index, record in enumerate(manifest["documents"], start=1):
        output = work_dir.resolve() / "drafts" / f"{record['doc_id']}.json"
        current = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
        has_values = current and normalize_data(current.get("data")) != empty_data()
        if has_values and not overwrite:
            skipped += 1
            continue
        print(f"[{index}/{len(manifest['documents'])}] drafting {record['source_filename']}", flush=True)
        try:
            coordinate = extract_pdf(Path(record["source_path"]), languages)
            parsed = parse_invoice_hybrid(coordinate["pages"], record["source_filename"], languages, mode="fast")
            annotation = new_annotation(record["source_filename"], record["source_sha256"], parsed)
            annotation["draft_pipeline_version"] = coordinate.get("pipeline_version")
            annotation["draft_quality"] = parsed.get("quality", {})
            output.write_text(json.dumps(annotation, ensure_ascii=False, indent=2), encoding="utf-8")
            completed += 1
        except Exception as error:  # Preserve progress across a large private batch.
            failure = work_dir.resolve() / "failures" / f"{record['doc_id']}.txt"
            failure.write_text(f"{type(error).__name__}: {error}\n", encoding="utf-8")
            failed += 1
    return {"completed": completed, "skipped": skipped, "failed": failed}


def _load_eligible(work_dir: Path, require_all_verified: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    manifest = load_manifest(work_dir)
    labels_root = Path(manifest["labels_root"])
    eligible: list[dict[str, Any]] = []
    problems: list[str] = []
    for record in manifest["documents"]:
        label_path = labels_root / f"{record['doc_id']}.json"
        if not label_path.exists():
            if require_all_verified:
                problems.append(f"{record['source_filename']}: verified label is missing")
            continue
        try:
            annotation = json.loads(label_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            problems.append(f"{record['source_filename']}: invalid label JSON ({error})")
            continue
        errors, _ = validate_annotation(annotation, require_verified=True)
        if annotation.get("source_filename") != record["source_filename"]:
            errors.append("source_filename does not match manifest")
        if annotation.get("source_sha256") != record["source_sha256"]:
            errors.append("source hash changed after annotation")
        if errors:
            problems.extend(f"{record['source_filename']}: {error}" for error in errors)
            continue
        if annotation.get("include_in_training") is True:
            eligible.append({**record, "annotation": annotation})
    return eligible, problems


def validation_report(work_dir: Path) -> dict[str, Any]:
    manifest = load_manifest(work_dir)
    labels_root = Path(manifest["labels_root"])
    verified = included = 0
    errors: list[str] = []
    warnings: list[str] = []
    for record in manifest["documents"]:
        label_path = labels_root / f"{record['doc_id']}.json"
        if not label_path.exists():
            continue
        try:
            annotation = json.loads(label_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"{record['source_filename']}: {error}")
            continue
        label_errors, label_warnings = validate_annotation(annotation)
        if annotation.get("source_sha256") != record["source_sha256"]:
            label_errors.append("source hash changed after annotation")
        verified += annotation.get("verified") is True
        included += annotation.get("verified") is True and annotation.get("include_in_training") is True
        errors.extend(f"{record['source_filename']}: {message}" for message in label_errors)
        warnings.extend(f"{record['source_filename']}: {message}" for message in label_warnings)
    return {
        "documents": len(manifest["documents"]),
        "verified": verified,
        "included": included,
        "unverified": len(manifest["documents"]) - verified,
        "errors": errors,
        "warnings": warnings,
    }


def _group_keys(entry: dict[str, Any]) -> tuple[str, ...]:
    """Both layout and supplier identity constrain a split, even when both exist."""
    annotation = entry["annotation"]
    explicit = str(annotation.get("layout_group") or "").strip().casefold()
    supplier = annotation["data"]["supplier"]
    vat = re.sub(r"\D", "", str(supplier.get("vat_number") or ""))
    name = " ".join(str(supplier.get(key) or "") for key in ("name_ar", "name_en"))
    name = " ".join(name.casefold().split())
    keys = ([f"layout:{explicit}"] if explicit else []) + ([f"vat:{vat}"] if vat else [])
    keys.append(f"hash:{entry['source_sha256']}")
    if not vat and name:
        keys.append(f"supplier:{name}")
    return tuple(keys)


def split_entries(entries: list[dict[str, Any]], seed: str = "invoice-ocr-v1") -> dict[str, list[dict[str, Any]]]:
    # Connected components close the loophole where a supplier has multiple
    # layout_group values or one layout spans multiple suppliers.
    parent = list(range(len(entries)))
    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    owners: dict[str, int] = {}
    for index, entry in enumerate(entries):
        for key in _group_keys(entry):
            if key in owners:
                parent[find(index)] = find(owners[key])
            else:
                owners[key] = index
    component_keys: dict[int, set[str]] = defaultdict(set)
    for index, entry in enumerate(entries):
        component_keys[find(index)].update(_group_keys(entry))
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, entry in enumerate(entries):
        root = find(index)
        key = min(component_keys[root])
        groups[key].append(entry)
    if len(groups) < 3:
        raise ValueError("At least three independent supplier/layout groups are required for train/validation/test")
    total = len(entries)
    target = {"train": total * 0.70, "validation": total * 0.15, "test": total * 0.15}
    result: dict[str, list[dict[str, Any]]] = {key: [] for key in target}
    ordered = sorted(groups.items(), key=lambda pair: (-len(pair[1]), hashlib.sha256(f"{seed}:{pair[0]}".encode()).hexdigest()))
    for group, members in ordered:
        choices = sorted(target, key=lambda key: (len(result[key]) / target[key], len(result[key]), key))
        result[choices[0]].extend({**member, "group": group} for member in members)
    return result


def _qwen_record(entry: dict[str, Any]) -> dict[str, Any]:
    images = entry["pages"]
    media: str | list[str] = images[0] if len(images) == 1 else images
    tags = "\n".join("<image>" for _ in images)
    return {
        "image": media,
        "conversations": [
            {"from": "human", "value": f"{tags}\n{PROMPT}"},
            {"from": "gpt", "value": json.dumps(entry["annotation"]["data"], ensure_ascii=False, separators=(",", ":"))},
        ],
    }


def export_qwen(work_dir: Path, min_verified: int = 80, seed: str = "invoice-ocr-v1") -> dict[str, Any]:
    work_dir = work_dir.resolve()
    entries, problems = _load_eligible(work_dir)
    if problems:
        raise ValueError("Label validation failed:\n" + "\n".join(problems[:30]))
    if len(entries) < min_verified:
        raise ValueError(f"Only {len(entries)} verified/included documents; at least {min_verified} are required")
    changed_sources = [entry["source_filename"] for entry in entries
                       if not Path(entry["source_path"]).exists()
                       or _sha256(Path(entry["source_path"])) != entry["source_sha256"]]
    if changed_sources:
        raise ValueError(f"Source PDF missing or changed since verification: {changed_sources[0]}")
    missing_images = [path for entry in entries for path in entry["pages"] if not Path(path).exists()]
    if missing_images:
        raise ValueError(f"Rendered page image is missing: {missing_images[0]}")
    splits = split_entries(entries, seed)
    minimum_held_out = max(1, min_verified // 8)
    for split in ("validation", "test"):
        if len(splits[split]) < minimum_held_out:
            raise ValueError(f"{split} has only {len(splits[split])} documents; need at least {minimum_held_out}. Add independent supplier/layout groups.")
    exports = work_dir / "exports"
    exports.mkdir(exist_ok=True)
    manifest_rows: list[dict[str, Any]] = []
    for split, members in splits.items():
        (exports / f"qwen_{split}.json").write_text(
            json.dumps([_qwen_record(entry) for entry in members], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for entry in members:
            manifest_rows.append({
                "doc_id": entry["doc_id"], "source_filename": entry["source_filename"],
                "source_sha256": entry["source_sha256"], "group": entry["group"], "split": split,
                "pages": entry["pages"], "target": entry["annotation"]["data"],
            })
    (exports / "evaluation_manifest.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in manifest_rows), encoding="utf-8"
    )
    summary = {
        "annotation_version": ANNOTATION_VERSION,
        "prompt_version": PROMPT_VERSION,
        "seed": seed,
        "counts": {key: len(value) for key, value in splits.items()},
        "group_counts": {key: len({entry["group"] for entry in value}) for key, value in splits.items()},
    }
    (exports / "export_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="render PDFs and create private annotation drafts")
    prepare.add_argument("--pdf-dir", type=Path, required=True)
    prepare.add_argument("--work-dir", type=Path, required=True)
    prepare.add_argument("--dpi", type=int, default=200)
    prepare.add_argument("--allow-public-pdf-dir", action="store_true")
    prepare.add_argument("--labels-dir", type=Path)
    prepare.add_argument("--allow-public-labels", action="store_true")
    draft = sub.add_parser("draft", help="run the existing OCR to prefill editable labels")
    draft.add_argument("--work-dir", type=Path, required=True)
    draft.add_argument("--languages", default="ara+eng")
    draft.add_argument("--overwrite", action="store_true")
    validate = sub.add_parser("validate", help="validate saved annotations")
    validate.add_argument("--work-dir", type=Path, required=True)
    export = sub.add_parser("export-qwen", help="make leakage-resistant Qwen VL SFT splits")
    export.add_argument("--work-dir", type=Path, required=True)
    export.add_argument("--min-verified", type=int, default=80)
    export.add_argument("--seed", default="invoice-ocr-v1")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            manifest = prepare_workspace(args.pdf_dir, args.work_dir, args.dpi, args.allow_public_pdf_dir,
                                         args.labels_dir, args.allow_public_labels)
            print(json.dumps({"documents": len(manifest["documents"]), "work_dir": str(args.work_dir.resolve())}, indent=2))
        elif args.command == "draft":
            print(json.dumps(draft_workspace(args.work_dir, args.languages, args.overwrite), indent=2))
        elif args.command == "validate":
            report = validation_report(args.work_dir)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if report["errors"] else 0
        elif args.command == "export-qwen":
            print(json.dumps(export_qwen(args.work_dir, args.min_verified, args.seed), indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
