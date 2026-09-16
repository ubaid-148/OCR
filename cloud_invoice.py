"""Optional one-request Claude PDF extraction; no local training or OCR."""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any

from invoice_response import clean_invoice_response
from local_ai_parser import _validate
from visual_invoice import FULL_SCHEMA, normalize_full


API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_PDF_BYTES = 20 * 1024 * 1024  # Base64 plus JSON must fit the API request limit.


def strict_invoice_schema() -> dict[str, Any]:
    """Keep the full invoice schema below Claude's optional/union limits."""
    schema = deepcopy(FULL_SCHEMA)

    def simplify(node: dict[str, Any]) -> None:
        kind = node.get("type")
        if isinstance(kind, list):
            # Empty strings mean absent. normalize_full() maps them back to null.
            node["type"] = "string"
        if node.get("type") == "object":
            properties = node.get("properties", {})
            node["required"] = list(properties)
            for child in properties.values():
                simplify(child)
        elif node.get("type") == "array" and isinstance(node.get("items"), dict):
            simplify(node["items"])

    simplify(schema)
    return schema


PROMPT = (
    "Extract this entire invoice PDF into the requested JSON schema. Read every page, "
    "including the complete item table and footer. Work for ANY supplier/layout; do not "
    "assume a known template. Keep supplier and customer identities, addresses and VAT IDs "
    "separate. Preserve every printed Arabic/English name, product description, item code, "
    "quantity, price, discount, taxable amount, VAT and gross total in its own field and "
    "original row order. Important: some tables print taxable amount and VAT PER UNIT, while "
    "the gross column is the extended row total; transcribe printed column values, never "
    "recalculate or shift them. Keep invoice date distinct from supply date. Numeric fields "
    "must be strings containing just the printed number (e.g. '14.79'); use an empty string "
    "for an absent or unreadable value. Use an empty string for every absent text field, and "
    "[] for absent arrays. Do not invent translations or values. Put extra labelled source "
    "fields into other_fields with their PDF page number."
)


def extract_invoice_claude(pdf_path: str | Path, api_key: str, *,
                           model: str = DEFAULT_MODEL, max_tokens: int = 16384,
                           timeout: int = 600) -> dict[str, Any]:
    """Send one PDF to Claude and return source-preserving JSON plus checks."""
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if not api_key or not api_key.strip():
        raise ValueError("ANTHROPIC_API_KEY is required in Colab Secrets")
    if not 1024 <= max_tokens <= 32768:
        raise ValueError("max_tokens must be between 1024 and 32768")
    pdf = path.read_bytes()
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("Selected file is not a PDF")
    if len(pdf) > MAX_PDF_BYTES:
        raise ValueError("PDF exceeds the 20 MiB safety limit for a single API request")
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64",
             "media_type": "application/pdf", "data": base64.b64encode(pdf).decode("ascii")}},
            {"type": "text", "text": PROMPT},
        ]}],
        "output_config": {"format": {"type": "json_schema", "schema": strict_invoice_schema()}},
    }
    request = urllib.request.Request(
        API_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"},
    )
    started = perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            message = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"Claude API HTTP {error.code}: {detail}") from error
    if message.get("stop_reason") != "end_turn":
        raise ValueError(f"Claude PDF output was incomplete ({message.get('stop_reason')}); no partial invoice was accepted")
    content = next((block.get("text") for block in message.get("content", [])
                    if block.get("type") == "text" and isinstance(block.get("text"), str)), None)
    if not content:
        raise ValueError("Claude PDF response has no JSON text")
    raw = json.loads(content)
    data = normalize_full(raw, path.name, "eng+ara")
    validation, quality = _validate(data)
    data["validation"] = validation
    quality.update(parser="claude_pdf", model=message.get("model") or model,
                   local_ai_status="cloud_source_review_required",
                   needs_review=True, overall_status="needs_review")
    quality.setdefault("review_reasons", []).append(
        "Cloud transcription is not independently verified against the PDF; check critical fields before import.")
    if validation.get("line_vat_sum_matches") is False:
        quality["review_reasons"].append(
            "Printed line VAT sum differs from document VAT; source amounts were preserved.")
    payload = {"data": data, "quality": quality, "pipeline_version": "2026-09-claude-pdf-v1",
               "ocr_device": "not_used", "timings_seconds": {"total": round(perf_counter() - started, 3)}}
    result = clean_invoice_response(payload)
    result["model"] = quality["model"]
    result["validation"] = validation
    result["api_usage"] = message.get("usage", {})
    result["source_sha256"] = hashlib.sha256(pdf).hexdigest()
    return result
