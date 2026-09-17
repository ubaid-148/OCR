"""Local Ollama extraction pass returning an untrusted canonical draft."""
from __future__ import annotations

import json
import math
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SYSTEM_PROMPT = """You extract Saudi Arabic/English tax invoices. Return ONLY valid JSON matching this exact canonical schema: document_type, invoice_number, invoice_serial, invoice_date, date_of_supply, reference_no, payment_method, seller, customer, items, totals, vat_summary, currency, page_info, validation. Preserve printed values; use null when absent or uncertain. Do not calculate, correct, or invent values. Do not include bbox, confidence, evidence, commentary, or markdown."""


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Non-finite JSON number: {value}")
    return number


def _json_response(text: str) -> dict[str, Any]:
    candidates = [text.strip()]
    if "```" in text:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
            stripped = stripped.rsplit("```", 1)[0].strip()
        candidates.append(stripped)
    for candidate in candidates:
        try:
            value = json.loads(candidate, parse_constant=_reject_constant, parse_float=_finite_float)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            for key in ("seller", "customer", "totals", "vat_summary", "page_info", "validation"):
                if key in value and not isinstance(value[key], dict):
                    raise ValueError(f"Ollama field {key} must be an object")
            if "items" in value and (not isinstance(value["items"], list) or
                                     any(not isinstance(item, dict) for item in value["items"])):
                raise ValueError("Ollama items must be an array of objects")
            return value
    raise ValueError("Ollama returned malformed JSON after fence cleanup")


def extract_with_ollama(ocr_payload: Any, model: str, url: str = "http://localhost:11434/api/generate",
                        timeout: float = 180.0) -> dict[str, Any]:
    prompt = ("OCR evidence follows. Extract the invoice into the required canonical JSON. "
              "Treat OCR as evidence only and leave uncertain fields null.\n\n" +
              json.dumps(ocr_payload, ensure_ascii=False, separators=(",", ":")))
    request = Request(url, data=json.dumps({"model": model, "system": SYSTEM_PROMPT,
                                            "prompt": prompt, "stream": False, "format": "json",
                                            "options": {"temperature": 0}}).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"Ollama unavailable: {error}") from error
    if not isinstance(response_data, dict):
        raise ValueError("Ollama response must be an object")
    if response_data.get("done") is False or response_data.get("done_reason") == "length":
        raise ValueError("Ollama response was incomplete or truncated")
    text = response_data.get("response")
    if text is None and isinstance(response_data.get("message"), dict):
        text = response_data["message"].get("content")
    if not isinstance(text, str):
        raise ValueError("Ollama response did not contain JSON text")
    return _json_response(text)
