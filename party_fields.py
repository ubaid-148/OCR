"""Seller/customer label grids: address, registration and identifier fields.

ZATCA-style invoices print each party as a labelled grid (Building No, Street,
Postal Code, CR, ...), either stacked (seller above customer) or side by side.
Values are paired with a label on the same row, inside the same party section,
and validated by shape. Existing values are never overwritten, except where the
parser evidently copied the other party's value.
"""
from __future__ import annotations

import re

from bbox_grouping import box_geometry
from invoice_formatter import DIGIT_TABLE, center, contains, has_arabic, normalize
from layout_invoice import geometry, header_hint, proof

SELLER_ANCHORS = ('seller', 'supplier', 'vendor', 'المورد', 'البائع', 'المريد')
CUSTOMER_ANCHORS = ('customer', 'buyer', 'bill to', 'sold to', 'cust name', 'cust.name', 'm/s',
                    'العميل', 'المشتري', 'السادة')

# field -> (aliases, kind, parties). Aliases are matched as a prefix/suffix too,
# because Arabic OCR frequently joins a label to its value ("المبنى6595").
FIELDS = {
    'building_no': (('building no', 'building number', 'building', 'bldg no', 'bldg',
                     'رقم المبنى', 'رقم المبني', 'المبنى', 'المبني'), 'building', 'both'),
    'street': (('street name', 'street', 'اسم الشارع', 'إسم الشارع', 'الشارع'), 'text', 'both'),
    'area': (('district', 'area', 'neighborhood', 'الحي', 'الجي'), 'text', 'both'),
    'post_code': (('postal code', 'post code', 'zip code', 'الرمز البريدي'), 'post', 'both'),
    'additional_no': (('additional no', 'additional number', 'add no', 'الرقم الإضافي للعنوان',
                       'الرقم الإضافي', 'الرقم الاضافي'), 'building', 'both'),
    'short_address': (('short address', 'short adrs', 'shrt adrs', 'العنوان المختصر', 'عنوان مختصر'),
                      'short', 'both'),
    'country': (('country', 'الدولة', 'البلد'), 'text', 'both'),
    'city': (('city', 'المدينة'), 'text', 'both'),
    'vat_number': (('vat number', 'vat no', 'vatnocust', 'tax code', 'tax number', 'trn',
                    'الرقم الضريبي', 'رقم الضريبة', 'الرقم الضريبي للعميل', 'رقم الضريبة للعميل'),
                   'vat', 'both'),
    'cr_number': (('commercial registration', 'cr no', 'cr number', 'c r', 'other seller id',
                   'other buyer id', 'السجل التجاري', 'السجل التجاري للعميل'), 'cr', 'both'),
    'customer_code': (('customer code', 'cus code', 'customer no', 'كود العميل', 'رقم العميل'),
                      'code', 'customer'),
    'name': (('customer name', 'name', 'اسم العميل', 'الاسم', 'الإسم'), 'text', 'customer'),
    'address': (('customer address', 'address', 'عنوان العميل'), 'text', 'customer'),
}
ALL_ALIASES = tuple(alias for aliases, _, _ in FIELDS.values() for alias in aliases)
# Labels of other header fields; a value search must not cross them.
OTHER_LABELS = ('name', 'الاسم', 'الإسم', 'due', 'phone', 'mobile', 'tel', 'fax', 'email', 'contact',
                'invoice', 'date', 'payment', 'الفاتورة', 'التاريخ', 'الدفع', 'جوال', 'هاتف')


HARAKAT = re.compile('[ً-ْـ]')  # diacritics and tatweel


def _clean(text):
    return HARAKAT.sub('', normalize(text))


def _squash(text):
    text = re.sub('[أإآٱ]', 'ا', _clean(text).casefold())
    return re.sub(r'[^0-9a-z؀-ۿ]+', '', text)


def _label_match(text, aliases):
    """Return (alias, remainder) when ``text`` starts or ends with a label alias."""
    raw = _clean(text).strip(' :：/|.-')
    folded = raw.casefold()
    for alias in sorted(aliases, key=len, reverse=True):
        a = alias.casefold()
        latin = bool(re.search('[a-z]', a))
        if latin:
            pattern = r'^' + r'[\s.]*'.join(re.escape(part) for part in a.split()) + r'(?![a-z])'
            m = re.search(pattern, folded)
            if m:
                return alias, raw[m.end():].strip(' :：/|.-#')
            m = re.search(r'(?<![a-z])' + r'[\s.]*'.join(re.escape(part) for part in a.split()) + r'[\s.:：]*$', folded)
            if m and m.start() > 0:
                # "Customer Street" / "Cust.Name": the prefix qualifies the label.
                return alias, ''
            continue
        joined = a.replace(' ', r'\s*')
        m = re.match(r'^[A-Za-z]{0,2}\s*' + joined, raw)  # stray OCR prefix, e.g. "Cالدولة"
        if m:
            return alias, raw[m.end():].strip(' :：/|.-#')
        m = re.search(joined + r'\s*$', raw)
        if m and m.start() > 0:
            return alias, raw[:m.start()].strip(' :：/|.-#')
    return None


def _value(text, kind):
    """Normalized value when ``text`` has the shape required by ``kind``."""
    value = normalize(text).translate(DIGIT_TABLE).strip(' :：/|.,-#')
    digits = re.sub(r'\s', '', value)
    if kind == 'vat':
        m = re.search(r'(?<!\d)(3\d{13}3)(?!\d)', digits)
        return m[1] if m else None
    if kind == 'cr':
        m = re.fullmatch(r'(\d{10})', digits)
        return m[1] if m else None
    if kind == 'building':
        # "Bldg. No:3518,Thirteenth" prints the street after the number.
        m = re.match(r'(\d{4})(?!\d)(?:[\s.,]+[A-Za-z؀-ۿ].*)?$', value)
        return m[1] if m else None
    if kind == 'post':
        return digits if re.fullmatch(r'\d{5}', digits) else None
    if kind == 'short':
        m = re.fullmatch(r'([A-Za-z]{4})\s*(\d{4})', value)
        return (m[1] + m[2]).upper() if m else None
    if kind == 'code':
        return value if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{1,19}', value) and re.search(r'\d', value) else None
    # Free text: letters, not a number and not itself another label.
    if has_arabic(value):
        value = re.sub(r'^[A-Za-z]{1,3}(?=[؀-ۿ])|(?<=[؀-ۿ])[A-Za-z]{1,3}$', '', value).strip()
    squashed = _squash(value)
    if contains(value, ('customer', 'seller', 'supplier', 'العميل', 'للعميل', 'المورد', 'البائع')):
        return None  # A party-qualified label such as "شارع العميل".
    if any(len(_squash(alias)) >= 5 and _squash(alias) in squashed for alias in ALL_ALIASES):
        return None  # Contains another label: an OCR merge of neighbouring cells.
    if len(value) < 2 or not re.search(r'[A-Za-z؀-ۿ]', value) or re.search(r'\d{3,}', value):
        return None
    if _label_match(value, ALL_ALIASES + OTHER_LABELS) and len(_squash(value)) <= 12:
        return None
    return value


def _sections(words, h, y, page_width, table_y):
    """(party, predicate) regions of the header, or [] when no party anchor exists."""
    header = [w for w in words if y(w) < table_y]
    def first(aliases):
        found = [w for w in header if contains(w['text'], aliases) or
                 any(_squash(w['text']).startswith(_squash(a)) for a in aliases if has_arabic(a))]
        return min(found, key=lambda w: (y(w), w['left']), default=None)
    seller, customer = first(SELLER_ANCHORS), first(CUSTOMER_ANCHORS)
    if customer is None:
        return []
    top = y(customer) - h
    if seller is not None and abs(y(seller) - y(customer)) < h * 2 and \
            abs(center(seller)[0] - center(customer)[0]) > page_width * .3:
        split = (center(seller)[0] + center(customer)[0]) / 2
        seller_left = center(seller)[0] < split
        def side(w, left):
            return (center(w)[0] < split) == left
        return [('supplier', lambda w: top <= y(w) < table_y and side(w, seller_left)),
                ('customer', lambda w: top <= y(w) < table_y and side(w, not seller_left))]
    seller_top = y(seller) - h if seller is not None and y(seller) < top else -1
    return [('supplier', lambda w: seller_top <= y(w) < top),
            ('customer', lambda w: top <= y(w) < table_y)]


def _pair(pool, h, y, field, aliases, kind):
    """Best (value, word, joined) for one field inside one party region."""
    labels = []
    for w in pool:
        match = _label_match(w['text'], aliases)
        if match:
            labels.append((w, match[1]))
    found = []
    for label, remainder in sorted(labels, key=lambda item: (y(item[0]), item[0]['left'])):
        joined = _value(remainder, kind) if remainder else None
        if joined and kind == 'text' and has_arabic(label['text']) != has_arabic(remainder):
            joined = None  # Latin OCR noise glued to an Arabic label is not its value.
        if joined:
            found.append((0, joined, label, True))
            continue
        arabic_label = has_arabic(label['text']) and not re.search('[A-Za-z]{3}', label['text'])
        row = [w for w in pool if w is not label and abs(y(w) - y(label)) < h * .7]
        for direction in ((-1, 1) if arabic_label else (1, -1)):
            if direction < 0:
                side = [w for w in row if center(w)[0] < center(label)[0]]
                side.sort(key=lambda w: label['left'] - (w['left'] + w['width']))
            else:
                side = [w for w in row if center(w)[0] > center(label)[0]]
                side.sort(key=lambda w: w['left'] - (label['left'] + label['width']))
            for w in side:
                gap = (label['left'] - (w['left'] + w['width'])) if direction < 0 else (w['left'] - (label['left'] + label['width']))
                if gap > h * 12:
                    break
                value = _value(w['text'], kind)
                if value:
                    found.append((1, value, w, False))
                    break
                if _label_match(w['text'], ALL_ALIASES + OTHER_LABELS):
                    break  # Another label: this row's value is missing.
            else:
                continue
            break
        if found:
            break
    if not found:
        return None
    # Prefer a separate value box over one joined to its label (less OCR merging).
    _, value, word, joined = min(found, key=lambda item: item[0])
    return value, word, joined


INVOICE_FIELDS = {
    'ref_no': (('customer ref no', 'reference no', 'reference number', 'ref no', 'رقم المرجع'), 'code'),
}


def extract_invoice_fields(pages):
    """{field: (value, word, joined)} for invoice-level labelled references."""
    found = {}
    for index, page in enumerate(pages[:1]):
        words = []
        for w in page.get('words', []):
            left, top, width, height = box_geometry(w)
            words.append(dict(w, left=left, top=top, width=width, height=height,
                              _page=page.get('page', index + 1)))
        if not words:
            continue
        h, y = geometry(words)
        table_y = header_hint(words) or max(y(w) for w in words) * .55
        pool = [w for w in words if y(w) < table_y]
        for field, (aliases, kind) in INVOICE_FIELDS.items():
            paired = _pair(pool, h, y, field, aliases, kind)
            if paired:
                found[field] = paired
    return found


def extract_party_fields(pages):
    """{party: {field: (value, evidence_word, joined)}} from the first invoice page."""
    result = {'supplier': {}, 'customer': {}}
    for index, page in enumerate(pages[:1]):
        words = []
        for w in page.get('words', []):
            left, top, width, height = box_geometry(w)
            words.append(dict(w, left=left, top=top, width=width, height=height,
                              _page=page.get('page', index + 1)))
        if not words:
            continue
        h, y = geometry(words)
        width = max((w['left'] + w['width'] for w in words), default=1)
        table_y = header_hint(words) or max(y(w) for w in words) * .55
        for party, inside in _sections(words, h, y, width, table_y):
            pool = [w for w in words if inside(w)]
            for field, (aliases, kind, parties) in FIELDS.items():
                if parties not in ('both', party):
                    continue
                paired = _pair(pool, h, y, field, aliases, kind)
                if paired:
                    result[party][field] = paired
    return result


def apply_party_fields(result, pages):
    """Fill empty party fields from labelled grids; flag weak or conflicting readings."""
    data, quality = result['data'], result['quality']
    evidence = data.setdefault('field_evidence', {})
    reasons = quality.setdefault('review_reasons', [])
    low = quality.setdefault('low_confidence_fields', [])
    _fix_crossed_names(data, reasons)
    found = extract_party_fields(pages)
    other = {'supplier': 'customer', 'customer': 'supplier'}
    for party, fields in found.items():
        target = data.setdefault(party, {})
        for field, (value, word, joined) in fields.items():
            existing = target.get(field)
            if field == 'cr_number':
                existing = existing or target.get('commercial_registration')
            if existing not in (None, ''):
                if _squash(existing) == _squash(value):
                    continue
                crossed = found[other[party]].get(field)
                if crossed and _squash(crossed[0]) == _squash(existing):
                    # The parser copied the other party's labelled value here.
                    reasons.append(f'{party}.{field}: replaced {existing}, which is the '
                                   f'{other[party]}\'s labelled value, with {value}.')
                else:
                    reasons.append(f'{party}.{field}: labelled grid reads {value}; '
                                   f'retained {existing}. Verify against the PDF.')
                    quality.update(needs_review=True, overall_status='needs_review')
                    continue
            target[field] = value
            evidence[f'{party}.{field}'] = proof(word)
            confidence = float(word.get('confidence') or 0) if word.get('source') != 'native_text' else 100
            if joined or confidence < 85:
                low.append(dict(field=f'{party}.{field}', **proof(word)))
                quality.update(needs_review=True, overall_status='needs_review')
    _compose_address(data, found, evidence)
    invoice = data.setdefault('invoice', {})
    for field, (value, word, joined) in extract_invoice_fields(pages).items():
        if invoice.get(field) in (None, ''):
            invoice[field] = value
            evidence[f'invoice.{field}'] = proof(word)
            if joined or (word.get('source') != 'native_text' and float(word.get('confidence') or 0) < 85):
                low.append(dict(field=f'invoice.{field}', **proof(word)))
                quality.update(needs_review=True, overall_status='needs_review')
    return result


ADDRESS_ORDER = ('building_no', 'street', 'area', 'city', 'post_code', 'country')


def _compose_address(data, found, evidence):
    """Prefer the labelled grid over a free-text address scraped from the section."""
    for party, fields in found.items():
        parts = [fields[key] for key in ADDRESS_ORDER if key in fields]
        if len(parts) >= 2:
            data[party]['address'] = ', '.join(value for value, _, _ in parts)
            evidence[f'{party}.address'] = [proof(word) for _, word, _ in parts]
    customer = data.get('customer', {})
    if customer.get('address') and customer.get('address') == customer.get('customer_code'):
        customer['address'] = None
        evidence.pop('customer.address', None)


def _fix_crossed_names(data, reasons):
    """A customer name identical to the supplier's own name is a mis-pairing."""
    supplier, customer = data.get('supplier', {}), data.get('customer', {})
    name = customer.get('name')
    if name and any(_squash(name) == _squash(s) for s in (supplier.get('name_en'), supplier.get('name_ar')) if s):
        customer['name'] = None
        data.get('field_evidence', {}).pop('customer.name', None)
        reasons.append('customer.name matched the supplier name and was cleared; check the customer block.')
