"""Check AI numeric values and identifiers against OCR before accepting them."""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from invoice_formatter import DIGIT_TABLE


def normalized(text):
    text = str(text).translate(DIGIT_TABLE).replace("٬", "").replace("٫", ".")
    text = re.sub(r"(?<=\d),(?=\d{3}(?:[, .]|$))", "", text)
    return " ".join(text.casefold().split())


def date_key(text):
    text = normalized(text).replace("/", "-")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def audit_ai(data, pages):
    """Null unsupported values in place; return review reasons and OCR evidence.

    Occurrence is necessary but not sufficient: role/column correctness remains
    the parser's responsibility and arithmetic checks still run afterwards.
    """
    words = [(page.get("page", i+1), w) for i,page in enumerate(pages) for w in page.get("words", [])]
    numeric_index = {}
    for page, word in words:
        for token in re.findall(r"(?<!\w)\d+(?:[.,]\d+)?(?!\w)", normalized(word.get("text", ""))):
            value = Decimal(token.replace(",", "."))
            numeric_index.setdefault(value, []).append((page, word))
    issues, evidence = [], {}

    def check(container, key, path, matches):
        value = container.get(key)
        if value is None:
            return
        if not matches:
            container[key] = None
            issues.append({"field": path, "reason": "AI value was not found in the OCR evidence"})
            return
        page, word = max(matches, key=lambda pair: float(pair[1].get("confidence", 0)))
        confidence = float(word.get("confidence", 0))
        evidence[path] = dict(page=page, text=word.get("text", ""), confidence=confidence)
        if confidence < 80:
            issues.append({"field": path, "reason": "Supporting OCR confidence is below 80%", "confidence": confidence})

    for section, key in (("supplier", "vat_number"), ("customer", "vat_number"), ("invoice", "invoice_number")):
        container = data[section]
        value = normalized(container.get(key) or "")
        pattern = r"(?<![\w/-])"+re.escape(value)+r"(?![\w/-])"
        matches = [(p,w) for p,w in words if value and re.search(pattern, normalized(w.get("text", "")))]
        check(container, key, f"{section}.{key}", matches)
    value = data["invoice"].get("date")
    matches = [(p,w) for p,w in words if value and any(date_key(t)==date_key(value)
               for t in re.findall(r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})\b", normalized(w.get("text", ""))))]
    check(data["invoice"], "date", "invoice.date", matches)
    containers = [("totals", data["totals"], ("subtotal", "discount", "vat_rate", "vat_amount", "net_amount"))]
    containers += [(f"items[{i}]", item, ("quantity", "unit_price", "amount")) for i,item in enumerate(data.get("items", []))]
    for prefix, container, keys in containers:
        for key in keys:
            value = container.get(key)
            if value is None:
                continue
            # _validate runs first and rejects malformed sections/items. Reject
            # booleans and non-numeric strings here instead of coercing them.
            matches = numeric_index.get(Decimal(str(value)), []) if isinstance(value, (int,float)) and not isinstance(value,bool) else []
            check(container, key, f"{prefix}.{key}", matches)
    return issues, evidence
