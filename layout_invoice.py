"""Conservative invoice extraction using detected columns, not fixed coordinates."""
from __future__ import annotations

import re
from statistics import median

from invoice_formatter import DIGIT_TABLE, center, contains, has_arabic, normalize, number_string


ALIASES = {
    "description": ("description", "product", "item description", "وصف", "البيان"),
    "quantity": ("quantity", "qty", "الكمية", "كمية"),
    "unit_price": ("unit price", "rate", "price", "السعر", "سعر الوحدة"),
    "amount": ("taxable", "taxable value", "net amount", "line amount", "amount", "القيمة الخاضعة"),
    "item_code": ("item code", "sku", "product code", "رمز الصنف"),
}


def numeric(text):
    text = " ".join(text.translate(DIGIT_TABLE).replace("٬", "").replace("٫", ".").split())
    text = re.sub(r"(?<=\d),(?=\d{3}(?:[, .]|$))", "", text)
    text = text.replace(",", ".")
    # Values may carry units, but addresses, percentages and embedded IDs are not money.
    match = re.fullmatch(r"\s*[:#]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:[A-Za-z]+|ريال|#)?\s*", text)
    return float(match[1]) if match else None


def table(words):
    h = median([float(w.get("height", 20)) for w in words if w.get("height", 0) > 0] or [20])
    for q in words:
        if not contains(q["text"], ALIASES["quantity"]):
            continue
        band = [w for w in words if abs(center(w)[1] - center(q)[1]) <= 3 * h]
        headers = {key: next((w for w in band if contains(w["text"], aliases)), None)
                   for key, aliases in ALIASES.items()}
        if not all(headers[k] for k in ("description", "quantity", "unit_price", "amount")):
            continue
        # Tax-exclusive value wins over a generic Amount/Total including VAT.
        headers["amount"] = next((w for w in band if contains(w["text"], ("taxable", "القيمة الخاضعة"))), headers["amount"])
        hx = {k: center(w)[0] for k, w in headers.items() if w}
        if len(set(hx.values())) != len(hx):
            continue
        header_y = max(center(w)[1] for w in headers.values() if w)
        gap = min(abs(a-b) for a in hx.values() for b in hx.values() if a != b)
        tolerance = max(h, gap * .55)
        # Stop at a footer summary or another table; repeat independently per page.
        stop = min((center(w)[1] for w in words
                    if center(w)[1] > header_y + 2*h and contains(w["text"],
                    ("subtotal", "grand total", "amount chargeable", "declaration", "الإفصاح", "إجمالي الفاتورة"))), default=float("inf"))
        body = [w for w in words if header_y + h*.6 < center(w)[1] < stop]
        anchors = [w for w in body if numeric(w["text"]) is not None
                   and abs(center(w)[0]-hx["quantity"]) <= tolerance]
        items = []
        anchors.sort(key=lambda w: center(w)[1])
        for row_index, anchor in enumerate(anchors):
            row = [w for w in body if abs(center(w)[1]-center(anchor)[1]) < h*.8]
            def cell(key):
                candidates = [w for w in row if numeric(w["text"]) is not None
                              and abs(center(w)[0]-hx[key]) <= tolerance]
                return min(candidates, key=lambda w: abs(center(w)[0]-hx[key]), default=None)
            price, amount = cell("unit_price"), cell("amount")
            desc = [w for w in row if numeric(w["text"]) is None
                    and min(hx, key=lambda k: abs(center(w)[0]-hx[k])) == "description"]
            if price is anchor:
                price = None
            if amount is anchor or (price is not None and amount is price):
                amount = None
            # Retain partially read rows so validation/AI can repair them. Dropping
            # a row hides the OCR failure and makes a short table look complete.
            if not desc or (price is None and amount is None):
                continue
            next_y = center(anchors[row_index+1])[1] if row_index+1 < len(anchors) else stop
            continuation = [w for w in body if h*.8 <= center(w)[1]-center(anchor)[1] <= h*2.5
                            and center(w)[1] < next_y-h*.8 and numeric(w["text"]) is None
                            and min(hx, key=lambda k: abs(center(w)[0]-hx[k])) == "description"
                            and not contains(w["text"], ("subtotal", "total", "vat", "tax", "الإجمالي"))]
            desc += continuation
            code = next((w["text"] for w in row if "item_code" in hx
                         and min(hx, key=lambda k: abs(center(w)[0]-hx[k])) == "item_code"), None)
            items.append(dict(line_no=len(items)+1, item_code=code,
                              description=" ".join(w["text"] for w in sorted(desc, key=lambda w: (round(center(w)[1]/h), w["left"]))),
                              quantity=numeric(anchor["text"]), unit_price=numeric(price["text"]) if price else None,
                              amount=numeric(amount["text"]) if amount else None))
        if items:
            return items, header_y, h
    return [], None, h


def parse_layout(pages, filename, language):
    if not pages:
        return None
    items = []
    first_header = None
    for page in pages:
        found, header_y, _ = table(page.get("words", []))
        if first_header is None and header_y is not None:
            first_header = header_y
        for item in found:
            item["line_no"] = len(items)+1
            items.append(item)
    if not items:
        return None  # Unknown table: let the hybrid parser handle it.
    words = sorted(pages[0].get("words", []), key=lambda w: center(w)[1])
    h = median([w.get("height", 20) for w in words] or [20])

    def near(label, predicate, pool=words):
        lx, ly = center(label)
        candidates = []
        for w in pool:
            if w is label or not predicate(w["text"]):
                continue
            x, y = center(w)
            same_row = abs(y-ly) <= h*.7
            below = 0 < y-ly <= h*2 and (w["left"] <= lx <= w["left"]+w["width"] or abs(w["left"]-label["left"]) < h*2)
            if same_row or below:
                candidates.append((abs(y-ly)*3 + abs(w["left"]-label["left"]), w))
        return min(candidates, key=lambda pair: pair[0])[1] if candidates else None

    invoice_no = None
    for w in words:
        if contains(w["text"], ("invoice no", "invoice number", "inv no", "رقم الفاتورة")):
            inline = re.search(r"[:#]\s*([\w/-]*\d[\w/-]*)\s*$", normalize(w["text"]))
            value = near(w, lambda s: bool(re.fullmatch(r"[A-Za-z0-9/-]*\d[A-Za-z0-9/-]*", normalize(s))))
            invoice_no = inline[1] if inline else normalize(value["text"]) if value else None
            if invoice_no:
                break
    date = None
    date_pattern = r"\b(?:\d{1,2}[-/]\d{1,2}[-/]20\d{2}|20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b"
    for label in words:
        if contains(label["text"], ("date", "dated", "invoice date", "التاريخ")) and not contains(label["text"], ("delivery", "due", "تسليم", "استحقاق")):
            match = re.search(date_pattern, normalize(label["text"]))
            value = near(label, lambda s: re.search(date_pattern, normalize(s)) is not None)
            if match or value:
                date = match[0] if match else re.search(date_pattern, normalize(value["text"]))[0]
                break
    buyer = next((w for w in words if contains(w["text"], ("buyer", "bill to", "customer", "المشتري", "العميل"))
                  and not contains(w["text"], ("seal", "signature", "ختم", "توقيع"))), None)
    buyer_name = near(buyer, lambda s: len(s)>8 and not contains(s, ("invoice", "date", "vat", "رقم", "التاريخ"))) if buyer else None
    vats = [(w, number_string(w["text"], {15})) for w in words]
    vats = [(w,v) for w,v in vats if v]
    supplier_vat = next((v for w,v in vats if not buyer or center(w)[1] < center(buyer)[1]), None)
    customer_vat = next((v for w,v in vats if buyer and center(buyer)[1] < center(w)[1] < first_header and v != supplier_vat), None)
    header_words = [w for w in words if center(w)[1] < min(center(buyer)[1] if buyer else first_header, center(vats[0][0])[1] if vats else first_header)]
    def company(arabic):
        return next((w["text"] for w in header_words if has_arabic(w["text"]) == arabic
                     and len(w["text"]) > 12 and not contains(w["text"], ("original", "invoice", "فاتورة"))), None)

    # Totals are label-associated values. Never turn building/registration numbers into money.
    all_words = pages[-1].get("words", [])
    def total(aliases):
        for label in reversed(sorted(all_words, key=lambda w: center(w)[1])):
            if contains(label["text"], aliases):
                value = near(label, lambda s: numeric(s) is not None, all_words)
                if value:
                    return numeric(value["text"])
        return None
    subtotal = total(("subtotal", "taxable value", "القيمة الخاضعة"))
    vat = total(("vat amount", "tax amount", "ضريبة القيمة", "ضريية القيمة"))
    net = total(("grand total", "invoice total", "amount due", "المبلغ المستحق", "إجمالي الفاتورة"))
    rates = {float(m[1]) for w in all_words if (m := re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", normalize(w["text"])))}
    text = " ".join(w["text"] for w in all_words)
    currency = next((c for c in ("SAR", "USD", "AED", "EUR", "GBP", "PKR") if re.search(r"\b"+c+r"\b", text)), None)
    if currency is None and ("Saudi Riyal" in text or "ريال سعودي" in text):
        currency = "SAR"
    return dict(document_type="invoice", document_language=language.split("+"), source_filename=filename,
                supplier=dict(name_ar=company(True), name_en=company(False), vat_number=supplier_vat),
                invoice=dict(invoice_number=invoice_no, date=date, hijri_date=None,
                             time=next((m[0] for w in words if (m := re.search(r"\b\d{2}:\d{2}(?::\d{2})?\b", w["text"]))), None), payment_method=None),
                customer=dict(name=buyer_name["text"] if buyer_name else None, vat_number=customer_vat, address=None),
                items=items, totals=dict(subtotal=subtotal, discount=total(("discount", "خصم")),
                                        vat_rate=next(iter(rates)) if len(rates)==1 else None,
                                        vat_amount=vat, net_amount=net, currency=currency))
