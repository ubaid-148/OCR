"""Printed amount-in-words lines (English and Arabic), checked against the net total.

A line is kept only when it names a currency and contains number words. When the
words convert to a number that matches totals.net_amount the reading is verified;
otherwise it is kept as printed and flagged for review. Nothing is inferred.
"""
from __future__ import annotations

import re
from decimal import Decimal

from invoice_formatter import normalize
from layout_invoice import proof

EN_UNITS = {w: i for i, w in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen '
    'fifteen sixteen seventeen eighteen nineteen'.split())}
EN_TENS = {w: 10 * i for i, w in enumerate('_ _ twenty thirty forty fifty sixty seventy eighty ninety'.split()) if i >= 2}
EN_SCALES = {'hundred': 100, 'thousand': 1000, 'thousands': 1000, 'million': 1_000_000}
EN_CURRENCY = re.compile(r'^(?:sar|riyals?|rials?|s\.?r\.?)$')
EN_HALALA = re.compile(r'^(?:halalas?|halala|h\.?)$')

AR_UNITS = {'صفر': 0, 'واحد': 1, 'احد': 1, 'اثنان': 2, 'اثنين': 2, 'اثنا': 2, 'اثني': 2,
            'ثلاث': 3, 'ثلاثة': 3, 'اربع': 4, 'اربعة': 4, 'خمس': 5, 'خمسة': 5, 'ست': 6, 'ستة': 6,
            'سبع': 7, 'سبعة': 7, 'ثمان': 8, 'ثماني': 8, 'ثمانية': 8, 'تسع': 9, 'تسعة': 9}
AR_TENS = {'عشر': 10, 'عشرة': 10, 'عشرون': 20, 'عشرين': 20, 'ثلاثون': 30, 'ثلاثين': 30,
           'اربعون': 40, 'اربعين': 40, 'خمسون': 50, 'خمسين': 50, 'ستون': 60, 'ستين': 60,
           'سبعون': 70, 'سبعين': 70, 'ثمانون': 80, 'ثمانين': 80, 'تسعون': 90, 'تسعين': 90}
AR_HUNDREDS = {'مائة': 100, 'مئة': 100, 'مائه': 100, 'مئه': 100, 'مائتان': 200, 'مئتان': 200,
               'مائتين': 200, 'مئتين': 200}
AR_THOUSANDS = {'الف': 1000, 'الاف': 1000, 'ألف': 1000, 'آلاف': 1000, 'الفان': 2000, 'الفين': 2000}
AR_NUMBER = re.compile('|'.join(sorted(map(re.escape, {*AR_UNITS, *AR_TENS, *AR_HUNDREDS, *AR_THOUSANDS}), key=len, reverse=True)))


def _en_run(tokens):
    total = current = 0
    for token in tokens:
        if token in EN_UNITS:
            current += EN_UNITS[token]
        elif token in EN_TENS:
            current += EN_TENS[token]
        elif token == 'hundred':
            current = (current or 1) * 100
        elif token in EN_SCALES:
            total += (current or 1) * EN_SCALES[token]
            current = 0
    return total + current


def parse_english(text):
    """SAR amount (Decimal) spelled in ``text``, or None when it cannot be read."""
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)  # "ThreeThousands"
    tokens = [t for t in re.split(r'[\s\-,/&]+', text.casefold()) if t]
    words = set(EN_UNITS) | set(EN_TENS) | set(EN_SCALES)
    riyal_run, halala_run, run, halala = [], [], [], None
    for i, token in enumerate(tokens):
        if token in words:
            run.append(token)
            continue
        if EN_HALALA.match(token) and (run or (i and tokens[i - 1].isdigit())):
            halala = _en_run(run) if run else int(tokens[i - 1])
            halala_run, run = run, []
            continue
        if token != 'and' and run:
            riyal_run = riyal_run or run
            run = []
    riyal_run = riyal_run or run
    if not riyal_run:
        return None
    return Decimal(_en_run(riyal_run)) + Decimal(halala or 0) / 100


def _ar_run(text):
    text = re.sub('[أإآ]', 'ا', text).replace('ـ', '')
    total = current = 0
    last_unit = False
    for token in re.split(r'\s+|(?<=\S)(?=\bو)', text):
        token = token.strip()
        token = token[1:] if token.startswith('و') and token[1:] and AR_NUMBER.match(token[1:]) else token
        if not token:
            continue
        # Compound hundreds such as خمسمائة / ثلاثمئة.
        m = re.fullmatch(r'(ثلاث|اربع|خمس|ست|سبع|ثمان|تسع)(مائة|مئة|مائه|مئه)', token)
        if m:
            current += AR_UNITS[m[1]] * 100
            last_unit = False
        elif token in AR_UNITS:
            current += AR_UNITS[token]
            last_unit = True
        elif token in ('عشر', 'عشرة') and last_unit:
            current += 10  # Teens: "خمسة عشر" = 5 + 10.
            last_unit = False
        elif token in AR_TENS:
            current += AR_TENS[token]
            last_unit = False
        elif token in AR_HUNDREDS:
            current += AR_HUNDREDS[token]
            last_unit = False
        elif token in AR_THOUSANDS:
            if AR_THOUSANDS[token] == 2000:
                total += 2000
            else:
                total += (current or 1) * 1000
            current = 0
            last_unit = False
        elif token in ('فقط', 'لا', 'غير', 'و'):
            continue
        else:
            return None  # An unknown word: do not guess the value.
    return total + current


def parse_arabic(text):
    text = re.sub('[أإآ]', 'ا', normalize(text))
    parts = re.split(r'(?:ريال|ربال)\S*', text, maxsplit=1)  # ربال: common OCR dot error
    if len(parts) != 2:
        return None
    riyal = _ar_run(re.sub(r'.*?(?=' + AR_NUMBER.pattern + ')', '', parts[0], count=1))
    if riyal is None:
        return None
    halala_text = re.sub(r'(سعودي\S*|هلل\S*|فقط|لا غير)', ' ', parts[1])
    halala = _ar_run(halala_text) if re.search(AR_NUMBER, halala_text) else 0
    if halala is None:
        return None
    return Decimal(riyal) + Decimal(halala) / 100


def _candidates(pages):
    seen = set()
    for index, page in enumerate(pages):
        for word in page.get('words', []):
            text = normalize(word.get('text', '')).strip()
            key = re.sub(r'\s+', '', text)
            if key in seen:
                continue
            latin = re.findall(r'[A-Za-z]+', text)
            if latin and any(EN_CURRENCY.match(t.casefold()) for t in re.split(r'[\s&]+', text)) and \
                    any(t.casefold() in EN_UNITS or t.casefold() in EN_TENS or
                        re.search(r'(?i)hundred|thousand', t) for t in latin):
                seen.add(key)
                yield 'en', text, parse_english(text), dict(word, _page=page.get('page', index + 1))
            elif re.search('ريال|ربال', text) and 'بالريال' not in text and len(AR_NUMBER.findall(re.sub('[أإآ]', 'ا', text))) >= 1 and \
                    not re.search(r'\d', text):
                seen.add(key)
                start = AR_NUMBER.search(re.sub('[أإآ]', 'ا', text))
                printed = text[start.start():] if start else text  # drop a leading label
                yield 'ar', printed, parse_arabic(text), dict(word, _page=page.get('page', index + 1))


def apply_amount_words(result, pages):
    data, quality = result['data'], result['quality']
    totals = data.setdefault('totals', {})
    evidence = data.setdefault('field_evidence', {})
    net = totals.get('net_amount')
    net = Decimal(str(net)) if net is not None else None
    found = {'en': [], 'ar': []}
    for language, text, value, word in _candidates(pages):
        found[language].append((text, value, word))
    targets = {'en': ('totals', 'amount_in_words'), 'ar': (None, 'amount_in_words_ar')}
    for language, options in found.items():
        if not options:
            continue
        section, key = targets[language]
        holder = totals if section else data
        if holder.get(key):
            continue
        matching = [o for o in options if net is not None and o[1] is not None and abs(o[1] - net) <= Decimal('.01')]
        # Without a net total to compare, prefer the largest spelled amount
        # (a separate "VAT amount in words" line is always smaller).
        text, value, word = matching[0] if matching else max(
            options, key=lambda o: (o[1] is not None, o[1] or 0))
        holder[key] = text
        path = f'{section}.{key}' if section else key
        evidence[path] = proof(word)
        if not matching:
            quality.update(needs_review=True, overall_status='needs_review')
            reason = (f'{path} could not be matched to the net total; check the printed words.'
                      if value is not None and net is not None else
                      f'{path} was read but not verified against a net total.')
            quality.setdefault('review_reasons', []).append(reason)
    return result
