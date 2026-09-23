"""Resolve item evidence only through a uniquely identified positioned table row."""
from layout_invoice import table
from invoice_formatter import normalize

NUMERIC_FIELDS = ('quantity', 'unit_price', 'amount', 'vat_amount', 'gross_amount', 'discount', 'tax_rate')


def scoped_item_evidence(data, pages):
    rows = []
    for index, page in enumerate(pages, 1):
        words = [dict(w, _page=page.get('page', index)) for w in page.get('words', [])]
        rows.extend(table(words)[0])
    result = {}
    items = data.get('items', [])
    for index, item in enumerate(items):
        key = 'item_code' if item.get('item_code') else 'description'
        value = normalize(str(item.get(key) or '')).casefold()
        if not value:
            continue
        matches = [row for row in rows if normalize(str(row.get(key) or '')).casefold() == value]
        duplicates = sum(normalize(str(row.get(key) or '')).casefold() == value for row in items)
        # Repeated SKUs/ambiguous descriptions require additional anchors; never
        # associate independent rows by list index or by numeric equality alone.
        if len(matches) != 1 or duplicates != 1:
            continue
        row = matches[0]
        for field in NUMERIC_FIELDS:
            proof = row.get('field_evidence', {}).get(field)
            if proof and row.get(field) == item.get(field):
                result[f'items[{index}].{field}'] = proof
    return result
