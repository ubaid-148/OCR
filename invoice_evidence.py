"""Check AI numeric values and identifiers against OCR before accepting them."""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from invoice_formatter import DIGIT_TABLE, center, contains


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
    """Retain values but flag unsupported/weak evidence for manual review.

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
            return False
        if not matches:
            issues.append({"field": path, "reason": "AI value was not found in the OCR evidence",
                           "needs_review": True})
            return False
        page, word = max(matches, key=lambda pair: float(pair[1].get("confidence") or 0))
        confidence = word.get("confidence")
        evidence[path] = dict(page=page, text=word.get("text", ""), confidence=confidence,
                              source=word.get("source", "ocr"),
                              bbox=[word.get(k) for k in ("left", "top", "width", "height")])
        if word.get("source") != "native_text" and float(confidence or 0) < 80:
            issues.append({"field": path, "reason": "Supporting OCR confidence is below 80%",
                           "confidence": confidence, "needs_review": True})
        return True

    for section, key in (("supplier", "vat_number"), ("customer", "vat_number"), ("invoice", "invoice_number")):
        container = data[section]
        value = normalized(container.get(key) or "")
        pattern = r"(?<![\w/-])"+re.escape(value)+r"(?![\w/-])"
        matches = [(p,w) for p,w in words if value and re.search(pattern, normalized(w.get("text", "")))]
        check(container, key, f"{section}.{key}", matches)
    code_positions=[]
    for index,item in enumerate(data.get('items',[])):
        code=normalized(item.get('item_code') or '')
        if not code:
            continue
        pattern=r'(?<![\w/-])'+re.escape(code)+r'(?![\w/-])'
        matches=[(p,w) for p,w in words if re.search(pattern,normalized(w.get('text','')))]
        check(item,'item_code',f'items[{index}].item_code',matches)
        # Enforce top-to-bottom item order only when the code has one
        # unambiguous positioned occurrence. Repeated SKU codes are allowed.
        if len(matches)==1 and 'top' in matches[0][1]:
            code_positions.append((index,matches[0][0],center(matches[0][1])[1],
                                   float(matches[0][1].get('height',0))))
    for previous,current in zip(code_positions,code_positions[1:]):
        if current[0]!=previous[0]+1:
            continue
        if current[1]==previous[1] and previous[2]>current[2]+max(previous[3],current[3])*.5:
            issues.append({'field':'items.row_order','reason':'Item codes are assigned to different printed rows',
                           'needs_review':True})
    # A customer address must be supported inside the buyer/customer section.
    # Merely finding its building number elsewhere on the page can silently map
    # the seller address to the customer, which is worse than returning null.
    address=data.get('customer',{}).get('address')
    address_numbers=[token for token in re.findall(r'(?<!\d)\d{3,10}(?!\d)',normalized(address or ''))]
    if address and address_numbers:
        anchors=[(p,w) for p,w in words if contains(w.get('text',''),(
            'buyer','bill to','customer','customer details','المشتري','العميل','تفاصيل العميل')) and
            not contains(w.get('text',''),('customer code','customer vat','tax','vat','كود العميل','الضريبي'))]
        limits={}
        for page_no,anchor in anchors:
            limits[(page_no,id(anchor))]=min((center(w)[1] for p,w in words if p==page_no and
                center(w)[1]>center(anchor)[1] and contains(w.get('text',''),(
                    'item description','description','product description','اسم الصنف','وصف الصنف'))),default=float('inf'))
        supported=[];unsupported=[]
        for token in address_numbers:
            token_matches=[(p,w) for p,w in words if re.search(r'(?<!\d)'+re.escape(token)+r'(?!\d)',normalized(w.get('text',''))) and
                           any(ap==p and center(anchor)[1]<=center(w)[1]<limits[(ap,id(anchor))]
                               for ap,anchor in anchors)]
            if token_matches:supported.extend(token_matches)
            else:unsupported.append(token)
        if unsupported:
            issues.append({'field':'customer.address','reason':'Some address numbers lack evidence in the customer section',
                           'needs_review':True})
        else:
            page,word=max(supported,key=lambda pair:float(pair[1].get('confidence') or 0))
            evidence['customer.address']=dict(page=page,text=word.get('text',''),confidence=word.get('confidence'),
                source=word.get('source','ocr'),bbox=[word.get(k) for k in ('left','top','width','height')])
    value = data["invoice"].get("date")
    matches = [(p,w) for p,w in words if value and any(date_key(t)==date_key(value)
               for t in re.findall(r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})\b", normalized(w.get("text", ""))))]
    check(data["invoice"], "date", "invoice.date", matches)
    value = data["invoice"].get("date_of_supply")
    matches = [(p,w) for p,w in words if value and any(date_key(t)==date_key(value)
               for t in re.findall(r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})\b", normalized(w.get("text", ""))))]
    check(data["invoice"], "date_of_supply", "invoice.date_of_supply", matches)
    containers = [("totals", data["totals"], ("subtotal", "discount", "vat_rate", "vat_amount", "net_amount"))]
    containers += [(f"items[{i}]", item, ("quantity", "unit_price", "amount", "vat_amount", "discount", "gross_amount")) for i,item in enumerate(data.get("items", []))]
    for prefix, container, keys in containers:
        for key in keys:
            value = container.get(key)
            if value is None:
                continue
            # _validate runs first and rejects malformed sections/items. Reject
            # booleans and non-numeric strings here instead of coercing them.
            matches = numeric_index.get(Decimal(str(value)), []) if isinstance(value, (int,float)) and not isinstance(value,bool) else []
            path = f"{prefix}.{key}"
            if check(container, key, path, matches) and matches:
                # A matching number anywhere on the page is not proof that it
                # belongs to this row/column. Layout-selected evidence bypasses
                # this audit; generic visual-AI occurrence matches stay review-only.
                issues.append({"field": path,
                               "reason": "Numeric value occurs in OCR, but its row/column role was not independently verified.",
                               "needs_review": True})
    return issues, evidence
