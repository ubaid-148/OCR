"""Fill missing document totals from printed amounts that satisfy the invoice arithmetic.

Total labels vary widely ("Total (SAR)", "Balance Due", "Total VAT.Excl", ...), and a
bare "Total" means the subtotal on one template and the net amount on another. When
labels leave a total empty, a *printed* number is accepted only if it makes
subtotal - discount + VAT = net hold to the cent. Values are never computed and
inserted; each reconciled total is listed for review.
"""
from __future__ import annotations

from decimal import Decimal

from layout_invoice import numeric, proof

TOLERANCE = Decimal('0.01')


def _printed_amounts(pages, integers=False):
    """Printed money values, bottom-most first on each page, last page first.

    Whole numbers ("100") are included only on request: alone they are too common
    (quantities, codes) to be matched without the stricter three-way check.
    """
    found = []
    for index, page in enumerate(pages):
        for word in page.get('words', []):
            text = str(word.get('text', ''))
            value = numeric(text)
            if value is None or (not integers and '.' not in text.replace(',', '.')) or \
                    (word.get('source') != 'native_text' and float(word.get('confidence') or 0) < 85):
                continue
            found.append((-index, -float(word.get('top', 0)), Decimal(str(value)),
                          dict(word, _page=page.get('page', index + 1))))
    found.sort(key=lambda item: (item[0], item[1]))
    return [(value, word) for _, _, value, word in found]


def _d(value):
    return Decimal(str(value)) if value is not None else None


def apply_totals_reconciliation(result, pages):
    data, quality = result['data'], result['quality']
    totals = data.setdefault('totals', {})
    evidence = data.setdefault('field_evidence', {})
    subtotal, vat, net = (_d(totals.get(k)) for k in ('subtotal', 'vat_amount', 'net_amount'))
    discount = _d(totals.get('discount')) or Decimal('0')
    other = _d(totals.get('other_charges')) or Decimal('0')
    if all(v is not None for v in (subtotal, vat, net)):
        return result
    amounts = _printed_amounts(pages)

    def find(target, exclude=()):
        return next(((value, word) for value, word in amounts
                     if abs(value - target) <= TOLERANCE and word not in exclude), None)

    filled = {}
    if subtotal is None and vat is not None and net is not None:
        filled['subtotal'] = find(net - vat - other + discount)
    elif net is None and subtotal is not None and vat is not None:
        filled['net_amount'] = find(subtotal - discount + vat + other)
    elif vat is None and subtotal is not None and net is not None:
        filled['vat_amount'] = find(net - subtotal + discount - other)
    elif subtotal is None and vat is None and net is None:
        # Nothing labelled: all three must appear as printed amounts that match
        # the item amounts, the item VAT and their sum.
        items = data.get('items') or []
        line_amounts = [_d(item.get('amount')) for item in items]
        line_vat = [_d(item.get('vat_amount')) for item in items]
        if items and all(a is not None for a in line_amounts + line_vat):
            s, v = sum(line_amounts, Decimal('0')), sum(line_vat, Decimal('0'))
            # The item cells themselves are not totals.
            cells = {(e.get('page'), round(float(e['bbox'][0])), round(float(e['bbox'][1])))
                     for item in items for e in (item.get('field_evidence') or {}).values()
                     if isinstance(e, dict) and len(e.get('bbox') or []) == 4}
            footer = [(value, word) for value, word in _printed_amounts(pages, integers=True)
                      if (word.get('_page'), round(float(word.get('left', 0))),
                          round(float(word.get('top', 0)))) not in cells]
            pick = lambda target, used=(): next(((value, word) for value, word in footer
                                                 if abs(value - target) <= TOLERANCE and
                                                 word not in used), None)
            sub = pick(s)
            tax = pick(v, [sub[1]] if sub else [])
            tot = pick(s + v, [m[1] for m in (sub, tax) if m])
            if sub and tax and tot and v > 0:
                filled.update(subtotal=sub, vat_amount=tax, net_amount=tot)
    elif vat is not None and subtotal is None and net is None:
        # Two unknowns: the item amounts pin the subtotal, the arithmetic the net.
        items = data.get('items') or []
        line_amounts = [_d(item.get('amount')) for item in items]
        if items and all(a is not None for a in line_amounts):
            items_sum = sum(line_amounts, Decimal('0'))
            sub = find(items_sum)
            tot = find(items_sum - discount + vat + other, exclude=[sub[1]] if sub else ())
            if sub and tot:
                filled['subtotal'], filled['net_amount'] = sub, tot
        rate = _d(totals.get('vat_rate'))
        if 'subtotal' not in filled and rate:
            # Otherwise the printed VAT rate pins the subtotal: subtotal x rate = VAT.
            for value, word in amounts:
                if value and abs((value - discount) * rate / 100 - vat) <= TOLERANCE:
                    tot = find(value - discount + vat + other, exclude=[word])
                    if tot:
                        filled['subtotal'], filled['net_amount'] = (value, word), tot
                        break
    for key, match in filled.items():
        if not match:
            continue
        value, word = match
        totals[key] = float(value)
        evidence[f'totals.{key}'] = proof(word)
        quality.setdefault('reconciled_fields', []).append(f'totals.{key}')
        quality.setdefault('review_reasons', []).append(
            f'totals.{key} {value} was matched to a printed amount by the invoice arithmetic, '
            'not by its label; confirm the label on the PDF.')
    return result
