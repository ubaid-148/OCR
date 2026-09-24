"""Bounded image rereads for fields/rows the first visual pass left unresolved."""
import base64
from copy import deepcopy
import io
from pathlib import Path
from layout_invoice import table, header_hint
from invoice_formatter import contains
from page_rotation import render_upright_page


def recovery_images(pdf_path, number, ocr_page):
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document.get_page(number - 1)
        try:
            source = render_upright_page(page, 300, ocr_page.get('rotation_degrees', 0))
            images = [source.copy(), source.crop((0, 0, source.width, int(source.height*.55))),
                      source.crop((0, int(source.height*.55), source.width, source.height))]
            encoded = []
            for image in images:
                image.thumbnail((3000, 3600))
                buffer = io.BytesIO()
                image.save(buffer, 'JPEG', quality=93)
                encoded.append(base64.b64encode(buffer.getvalue()).decode('ascii'))
                image.close()
            source.close()
            return encoded
        finally:
            page.close()
    finally:
        document.close()


def _present(value):
    return value not in (None, '', [])


def _fill_missing(original, candidate, prefix, notes):
    for key, value in candidate.items():
        path = prefix + '.' + key if prefix else key
        if isinstance(value, dict):
            if not isinstance(original.get(key), dict):
                original[key] = {}
            _fill_missing(original[key], value, path, notes)
        elif _present(value):
            if not _present(original.get(key)):
                original[key] = deepcopy(value)
            elif original[key] != value:
                notes.append(f'Image rereads disagree on {path}; retained the first reading.')


def merge_item_reread(existing, candidate):
    """Require unique ordered anchors; never merge independent rows by index."""
    if not existing:
        return deepcopy(candidate), []
    if not candidate:
        return existing, ['Item reread found fewer rows; retained the first reading.']
    keys = ('item_code',) if any(r.get('item_code') for r in existing + candidate) else ('description',)
    for key in keys:
        left = [str(r.get(key) or '').strip() for r in existing]
        right = [str(r.get(key) or '').strip() for r in candidate]
        if not all(left) or not all(right) or len(set(left)) != len(left) or len(set(right)) != len(right):
            continue
        if any(value not in right for value in left):
            continue
        positions = [right.index(value) for value in left]
        if positions != sorted(positions):
            continue
        merged = deepcopy(candidate)
        notes = []
        for row, position in zip(existing, positions):
            kept = deepcopy(row)
            _fill_missing(kept, candidate[position], f'items[{position}]', notes)
            merged[position] = kept
        return merged, notes
    return existing, ['Item reread could not be aligned uniquely in printed order; retained the first reading.']


def recover_page(pdf_path, number, count, raw, ocr_page, ask, diagnostics, progress=None):
    result = deepcopy(raw)
    notes = []
    invoice = raw.get('invoice') if isinstance(raw.get('invoice'), dict) else {}
    totals = raw.get('totals') if isinstance(raw.get('totals'), dict) else {}
    items = raw.get('items') if isinstance(raw.get('items'), list) else []
    rows, _, _ = table(ocr_page.get('words', []))
    header_missing = (number == 1 and any(not _present(invoice.get(k)) for k in ('invoice_number', 'date')))
    if number == 1:
        supplier = raw.get('supplier') or {}
        customer = raw.get('customer') or {}
        header_missing = header_missing or not any(supplier.get(k) for k in ('name_ar','name_en')) or not any(customer.get(k) for k in ('name','name_ar','name_en'))
    footer_present = any(contains(w.get('text', ''), ('subtotal','grand total','net amount','vat summary','المجموع','الإجمالي')) for w in ocr_page.get('words', []))
    totals_missing = (bool(totals) or footer_present) and any(not _present(totals.get(k)) for k in ('subtotal', 'vat_amount', 'net_amount'))
    item_missing = (len(items) < len(rows) or any(
                    not any(_present(item.get(k)) for k in ('description', 'description_ar', 'description_en'))
                    or any(not _present(item.get(k)) for k in ('quantity', 'unit_price', 'amount')) for item in items)
                    or not items and header_hint(ocr_page.get('words', [])) is not None)
    if not (header_missing or totals_missing or item_missing):
        return result, notes
    try:
        images = recovery_images(pdf_path, number, ocr_page)
    except (OSError, ValueError, RuntimeError) as error:
        return result, [f'Page {number} focused image render failed: {error}']
    context = ('Image 1 is the full original page. Images 2 and 3 are enlarged upper and lower '
               'regions of the SAME page, not additional pages. Read each printed value once. ')
    for scope, required in (('header', header_missing or totals_missing), ('items', item_missing)):
        if not required:
            continue
        if progress:
            progress(f'Rereading unresolved {scope} on page {number}/{count}')
        try:
            candidate = ask(images, number, count, scope, diagnostics=diagnostics,
                            instruction=context + 'Recheck faint or omitted fields, row boundaries and column labels. '
                            'Return only what is visible; do not infer missing prices, quantities or identifiers.')
            if scope == 'header':
                candidate = {k:v for k,v in candidate.items() if k != 'items' and not k.startswith('_')}
                _fill_missing(result, candidate, '', notes)
            else:
                candidate_items = candidate.get('items')
                if not isinstance(candidate_items, list) or not all(isinstance(r, dict) for r in candidate_items):
                    raise ValueError('Item reread did not return an array of objects')
                result['items'], conflicts = merge_item_reread(items, candidate_items)
                notes.extend(conflicts)
            notes.append(f'Page {number} {scope} received a focused image reread; recovered fields still require source review.')
        except (OSError, ValueError, RuntimeError) as error:
            notes.append(f'Page {number} {scope} reread failed; retained the initial extraction: {error}')
    return result, notes
