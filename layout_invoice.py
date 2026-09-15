"""Invoice fields from page-local, skew-aware columns and label/value evidence."""
from __future__ import annotations
import re
from decimal import Decimal
from statistics import median
from invoice_formatter import DIGIT_TABLE, center, contains, has_arabic, normalize, number_string
from document_regions import invoice_words

ALIASES = {
    'description': ('item description','description','product','product description','وصف','الوصف','البيان','اسم الصنف','وصف الصنف'),
    'quantity': ('quantity','qty','الكمية','كمية'),
    'unit_price': ('unit price','rate','price','price per unit','السعر','سعر الوحدة','سعر افرادي','سعر أفرادي','سعر الوحدة بدون الضريبة'),
    'amount': ('taxable value','taxable amount','taxable','line amount','net amount','amount','total','القيمة الخاضعة','المبلغ الخاضع','المبلغ الخاضع للضريبة','الاجمالي','الإجمالي'),
    'item_code': ('item code','item id','item no','sku','product code','material code','رمز الصنف','رقم الصنف','كود الصنف','رقم المنتج','كود المنتج','رقم المادة','كود المادة'),
    'serial': ('s no','sn','الرقم','مسلسل'),
    'unit': ('uom','unit','الوحدة'),
    'vat_amount': ('vat amount','tax amount','vat','الضريبة','قيمة الضريبة','قيمة الضريبية','مبلغ الضريبة'),
    'vat_rate': ('tax rate','vat rate','نسبة الضريبة','نسبة ضريبة القيمة المضافة'),
    'discount': ('discount','خصم'),
    'gross_amount': ('item subtotal','including vat','total including vat','المجموع شامل الضريبة','الإجمالي شامل الضريبة','المبلغ شامل الضريبة'),
}

INVOICE_LABELS = (
    'invoice no','invoice number','inv no','invoice serial','invoice serial no','serial invoice no',
    'رقم الفاتورة','مسلسل الفاتورة','رقم مسلسل الفاتورة','رقم تسلسل الفاتورة','الرقم التسلسلي للفاتورة',
)
DATE_LABELS = (
    'date','dated','invoice date','issue date','date and time','التاريخ','تاريخ','تاريخ الفاتورة',
    'تاريخ اصدار الفاتورة','تاريخ إصدار الفاتورة','تاريخ ووقت اصدار الفاتورة','تاريخ ووقت إصدار الفاتورة',
)
SUPPLY_DATE_LABELS = (
    'supply date','date of supply','delivery date',
    'تاريخ التوريد','تاريخ التسليم',
)
CUSTOMER_SECTION_LABELS = (
    'buyer','bill to','customer','customer details','customer name','cust name','cust.name','custname','customer code','cus code',
    'المشتري','العميل','السادة','تفاصيل العميل','تفاصيل العملاء','اسم العميل','كود العميل','رقم العميل',
)
CUSTOMER_NAME_LABELS = ('buyer name','customer name','cust name','cust.name','custname','name of customer','اسم العميل','اسم المشتري','اسم الزبون')
CUSTOMER_VAT_LABELS = (
    'customer vat','buyer vat','customer tax number','buyer tax number',
    'الرقم الضريبي للعميل','الرقم الضريبي للمشتري','رقم ضريبة العميل','الرقم الضريبي للزبون',
)
SUPPLIER_VAT_LABELS = (
    'supplier vat','seller vat','vat no','vat number','supplier tax number',
    'الرقم الضريبي للمورد','الرقم الضريبي للبائع','الرقم الضريبي','رقم ضريبة المورد',
)
ADDRESS_FIELDS = (
    ('building_no', ('building no','building','bldg','المبنى','مبنى'), 'number'),
    ('street', ('street','الشارع','شارع'), 'text'),
    ('area', ('area','district','الحي','حي','المنطقة'), 'text'),
    ('post_code', ('post code','postal code','zip code','الرمز البريدي'), 'number'),
    ('additional_no', ('additional no','add no','additional number','الرقم الإضافي'), 'number'),
    ('short_address', ('short address','short adrs','shrt adrs','عنوان مختصر','العنوان المختصر'), 'short'),
    ('country', ('country','الدولة'), 'text'),
    ('city', ('city','المدينة'), 'text'),
)


def numeric(text):
    text = ' '.join(text.translate(DIGIT_TABLE).replace('٬','').replace('٫','.').split())
    text = re.sub(r'(?<=\d),(?=\d{3}(?:[, .]|$))','',text).replace(',','.')
    m = re.fullmatch(r'\s*[:#]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:PCS?|SET|SETS|KG|M|LTR|SAR|USD|AED|ريال|#)?\s*',text,re.I)
    return float(m[1]) if m else None


def geometry(words):
    h = median([float(w.get('height',20)) for w in words if w.get('height',0)>0] or [20])
    slopes=[]
    for w in words:
        p=w.get('polygon',[])
        if len(p)>=2 and abs(p[1][0]-p[0][0]) > h*4:
            slope=(p[1][1]-p[0][1])/(p[1][0]-p[0][0])
            if abs(slope)<.15:
                slopes.append(slope)
    slope=median(slopes) if slopes else 0
    return h, lambda w: center(w)[1]-slope*center(w)[0]


def header_match(text,aliases):
    # Keep the original evidence; separate joined Arabic headers only for matching.
    separated=re.sub(r'(?<=\S)(الوحدة|الكمية|الضريبة|السعر|المبلغ|القيمة|الصنف|الخصم|النسبة)',r' \1',text)
    return contains(separated,aliases)


def proof(word):
    if word is None:
        return None
    return dict(page=word.get('_page',1), text=word['text'], confidence=word.get('confidence'),
                source=word.get('source','ocr'), bbox=[word.get(k,0) for k in ('left','top','width','height')])


def section_address(words, start_y, end_y, h, row_y=None):
    """Extract address components only inside a known buyer/customer section."""
    row_y=row_y or (lambda word:center(word)[1])
    pool=[w for w in words if start_y-h*.8<=row_y(w)<end_y]
    all_aliases=tuple(alias for _,aliases,_ in ADDRESS_FIELDS for alias in aliases)

    def strip_labels(text, aliases):
        value=normalize(text)
        for alias in sorted(set(all_aliases+aliases),key=len,reverse=True):
            if re.search(r'[A-Za-z]',alias):
                value=re.sub(r'(?i)(?<![A-Za-z])'+re.escape(alias)+r'(?![A-Za-z])',' ',value)
            else:
                value=value.replace(alias,' ')
        return re.sub(r'[/|:;_-]+',' ',value).strip(' .,:;/|-')

    def acceptable(text, kind):
        text=normalize(text).strip(' .,:;/|-')
        if not text or number_string(text,{15}) or contains(text,(
                'customer','invoice','tax code','vat','commercial registration','c.r','العميل',
                'الفاتورة','الضريبي','السجل التجاري')):
            return False
        if kind=='number':return bool(re.fullmatch(r'\d{3,10}',text))
        if kind=='short':return bool(re.fullmatch(r'(?i)[A-Z]{2,8}\s*\d{3,10}',text))
        return len(text)>2 and bool(re.search(r'[A-Za-z؀-ۿ]',text)) and not contains(text,all_aliases)

    parts=[];proofs=[]
    for _,aliases,kind in ADDRESS_FIELDS:
        labels=[w for w in pool if header_match(w['text'],aliases)]
        if not labels:continue
        label=min(labels,key=row_y)
        embedded=strip_labels(label['text'],aliases)
        chosen=label if acceptable(embedded,kind) else None
        value=embedded if chosen else None
        if chosen is None:
            candidates=[]
            for word in pool:
                if word is label or abs(row_y(word)-row_y(label))>h*.85:
                    continue
                candidate=strip_labels(word['text'],aliases)
                if acceptable(candidate,kind):
                    candidates.append((abs(center(word)[0]-center(label)[0]),word,candidate))
            if candidates:
                _,chosen,value=min(candidates,key=lambda item:item[0])
        if value and value not in parts:
            parts.append(value);proofs.append(proof(chosen))
    return ', '.join(parts) if parts else None,proofs


def header_hint(words):
    """Locate a likely header without requiring successfully parsed item rows."""
    h,y=geometry(words)
    for w in sorted(words,key=lambda w:(y(w),w.get('left',0))):
        if header_match(w['text'],ALIASES['description']):
            peers=[p for p in words if abs(y(p)-y(w))<h*3 and
                   any(header_match(p['text'],ALIASES[k]) for k in ('item_code','quantity','amount'))]
            if peers:return y(w)
    return None


def _header_rank(key, word, quantity_word, y):
    """Prefer a cell-local reread over a wide/merged base-OCR header.

    Bilingual ruled tables are frequently returned as one OCR box spanning two
    neighbouring headings. Its geometric centre then points at the wrong
    numeric column. Targeted cell OCR has the same text but trustworthy cell
    geometry, so it must win when available.
    """
    targeted = word.get('retry_kind') in {'table_cells', 'table_cells_en'}
    text = normalize(word['text'])
    exact_alias = any(text.casefold().strip(' :') == normalize(alias).casefold()
                      for alias in ALIASES[key])
    return (
        not targeted,
        not exact_alias,
        not contains(word['text'],('taxable','القيمة الخاضعة')) if key == 'amount' else False,
        not contains(word['text'],('قيمة','amount')) if key == 'vat_amount' else False,
        not bool(re.search('[A-Za-z]',word['text'])),
        abs(y(word)-y(quantity_word)),
        float(word.get('width', 0)),
        word['left'],
    )


def table(words):
    h,y=geometry(words)
    words=sorted(words,key=lambda w:(y(w),w.get('left',0),w['text']))
    for q in words:
        if not header_match(q['text'],ALIASES['quantity']):
            continue
        band=[w for w in words if abs(y(w)-y(q))<=3*h]
        headers={}
        for key,aliases in ALIASES.items():
            choices=[w for w in band if header_match(w['text'],aliases)]
            if key=='item_code' and not choices:
                choices=[w for w in band if w['text'].strip().casefold()=='item']
            if key=='item_code':
                choices=[w for w in choices if not contains(w['text'],('tax code','vat code','رمز الضريبة','كود الضريبة'))]
            if key=='unit':
                choices=[w for w in choices if not contains(w['text'],ALIASES['unit_price'])]
            if key=='unit_price':
                choices=[w for w in choices if not contains(w['text'],('tax rate',))]
            if key=='vat_amount':
                choices=[w for w in choices if not contains(w['text'],('tax rate','vat rate','without vat','including vat','شامل الضريبة','بدون الضريبة','نسبة الضريبة'))
                         and not (header_match(w['text'],ALIASES['amount']) and
                                  not contains(w['text'],('vat amount','tax amount','مبلغ الضريبة','قيمة الضريبة')))]
            if key=='amount':
                choices=[w for w in choices if not contains(w['text'],('vat amount','tax amount','total with vat','total including vat','total (excl) vat',
                                                                 'مبلغ الضريبة','قيمة الضريبة','شامل الضريبة'))]
            headers[key]=min(choices,key=lambda w:_header_rank(key,w,q,y),default=None)
        if not all(headers[k] for k in ('description','quantity','unit_price','amount')):
            continue
        hx={k:center(w)[0] for k,w in headers.items() if w}
        if len({hx[k] for k in ('description','quantity','unit_price','amount')})<4:
            continue
        for key in ('vat_amount','unit','serial','item_code','discount','vat_rate','gross_amount'):
            if key in hx and any(hx[key]==hx[k] for k in ('description','quantity','unit_price','amount')):
                hx.pop(key)
        header_y=max(y(headers[k]) for k in ('description','quantity','unit_price','amount'))
        stop=min((y(w) for w in words if y(w)>header_y+2*h and (normalize(w['text']).strip(' :').casefold() in {'total','مجموع'} or contains(w['text'],
                 ('subtotal','grand total','gross amount','total amount','total excluding vat',
                  'taxable total','total vat','total (excl) vat','الإجمالي بدون الضريبة','amount chargeable','declaration','الإفصاح','إجمالي الفاتورة')))),default=float('inf'))
        body=[w for w in words if header_y+h*.6<y(w)<stop]
        def tolerance(key):
            return max(h*.75,min(abs(hx[key]-x) for k,x in hx.items() if k!=key)*.58)
        anchors=[w for w in body if numeric(w['text']) is not None and abs(center(w)[0]-hx['quantity'])<=tolerance('quantity')]
        anchors.sort(key=lambda w:(y(w),abs(center(w)[0]-hx['quantity'])))
        unique=[]
        for w in anchors:
            if not unique or abs(y(w)-y(unique[-1]))>h*.7:
                unique.append(w)
        # Keep every printed description row, including gaps between readable quantities.
        for w in body:
            if numeric(w['text']) is None and w.get('height',h)<h*2 and abs(center(w)[0]-hx['description'])<tolerance('description') and not contains(w['text'],('total','vat','discount')):
                row_peers=[p for p in body if abs(y(p)-y(w))<h*1.15]
                numeric_peers=[p for p in row_peers if numeric(p['text']) is not None and any(abs(center(p)[0]-hx[k])<tolerance(k) for k in ('unit_price','amount'))]
                coded='item_code' in hx and any(abs(center(p)[0]-hx['item_code'])<tolerance('item_code') and
                    re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./#*+() -]{1,60}',normalize(p['text'])) for p in row_peers)
                if (len(numeric_peers)>=2 or coded) and all(abs(y(w)-y(a))>h*1.15 for a in unique):unique.append(w)
        unique.sort(key=y)
        items=[]
        exclusive=any(contains(w['text'],('taxable','without vat','القيمة الخاضعة')) and
                      abs(center(w)[0]-hx['amount'])<h*3 for w in band)
        gross_column=not exclusive and 'vat_amount' in hx and contains(headers['amount']['text'],('total amount','الاجمالي','الإجمالي'))
        for i,anchor in enumerate(unique):
            quantity_missing=numeric(anchor['text']) is None or abs(center(anchor)[0]-hx['quantity'])>tolerance('quantity')
            row=[w for w in body if abs(y(w)-y(anchor))<h*1.15]
            def cell(key):
                if key not in hx:
                    return None
                return min((w for w in row if numeric(w['text']) is not None and abs(center(w)[0]-hx[key])<=tolerance(key)),
                           key=lambda w:(w.get('source')!='targeted_ocr',abs(center(w)[0]-hx[key]),abs(y(w)-y(anchor)),w['text']),default=None)
            price,amount,tax,discount=cell('unit_price'),cell('amount'),cell('vat_amount'),cell('discount')
            if price is anchor: price=None
            if amount is anchor or amount is price: amount=None
            code=None
            code_candidates=[w for w in row if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./#*+() -]{1,60}',normalize(w['text'])) and ('item_code' in hx or re.search(r'\d',w['text']))]
            if 'item_code' in hx:
                code=min((w for w in code_candidates if abs(center(w)[0]-hx['item_code'])<=tolerance('item_code')),
                         key=lambda w:abs(center(w)[0]-hx['item_code']),default=None)
            else:
                # Without an explicit code header, a reordered Qty/Price/Amount
                # column to the left of Description is not an item-code column.
                # Only use a genuinely separate column, and require a code-like
                # token rather than a plain monetary/quantity value.
                known_numeric=('quantity','unit_price','amount','vat_amount','discount','gross_amount','vat_rate')
                code=min((w for w in code_candidates if w['left']<headers['description']['left'] and
                           center(w)[0]>hx.get('serial',-float('inf'))+h*.5 and
                           all(abs(center(w)[0]-hx[key])>tolerance(key) for key in known_numeric if key in hx) and
                           (bool(re.search(r'[A-Za-z]',normalize(w['text']))) or
                            bool(re.fullmatch(r'\d{3,}',normalize(w['text']))))),
                         key=lambda w:w['left'],default=None)
            # A description spans the free area between neighbor header edges.
            dx=hx['description']
            left=max((headers[k]['left']+headers[k]['width'] for k,x in hx.items() if x<dx),default=-float('inf'))
            right=min((headers[k]['left'] for k,x in hx.items() if x>dx),default=float('inf'))
            if code is not None and center(code)[0]<dx:
                left=max(left,code['left']+code['width'])
            def is_desc(w):
                return (w is not code and numeric(w['text']) is None and len(w['text'].strip())>1 and
                        left<center(w)[0]<right and not contains(w['text'],('subtotal','total','vat','tax')))
            desc=[w for w in row if is_desc(w)]
            next_y=y(unique[i+1]) if i+1<len(unique) else stop
            desc += [w for w in body if w not in row and h*1.15<=y(w)-y(anchor)<=h*2.5 and y(w)<next_y-h*.8 and is_desc(w)]
            if any(w.get('source')=='targeted_ocr' and has_arabic(w['text']) for w in desc):
                # Prefer the focused Arabic re-read over a low-confidence Latin
                # hallucination produced from the same faint dot-matrix text.
                desc=[w for w in desc if has_arabic(w['text']) or w.get('source')=='native_text' or float(w.get('confidence') or 0)>=85]
            if not desc or (price is None and amount is None):
                continue
            qty=None if quantity_missing else numeric(anchor['text']);pv=numeric(price['text']) if price else None
            if quantity_missing and price and price.get('source')=='targeted_ocr':
                pv=None  # A corrected faint price cannot be reconciled without quantity.
            av=numeric(amount['text']) if amount else None
            tv=numeric(tax['text']) if tax else None
            dv=numeric(discount['text']) if discount else None
            gross_word=cell('gross_amount')
            unit_word=min((w for w in row if 'unit' in hx and abs(center(w)[0]-hx['unit'])<tolerance('unit') and re.fullmatch(r'(?i)pcs?\.?|sets?|kg|m|ltr|box|roll',w['text'].strip())),key=lambda w:abs(center(w)[0]-hx['unit']),default=None)
            derived=False;total_is_pretax=False
            gross=av if gross_column else numeric(gross_word['text']) if gross_word else None
            if gross_column:
                av=None
                calculated=Decimal(str(qty))*Decimal(str(pv))-Decimal(str(dv or 0)) if qty is not None and pv is not None else None
                if calculated is not None and gross is not None and abs(calculated-Decimal(str(gross)))<=Decimal('.02'):
                    # With a blank per-line VAT cell, Total may still be the
                    # printed pre-tax amount (quantity x unit price).
                    av=gross;gross=None;total_is_pretax=True
                elif calculated is not None and tv is not None and gross is not None:
                    if abs(calculated+Decimal(str(tv))-Decimal(str(gross)))<=Decimal('.02'):
                        av=float(calculated);derived=True
            ev={k:proof(w) for k,w in [('quantity',None if quantity_missing else anchor),('unit_price',price),('amount',amount if not gross_column or total_is_pretax else None),
                                      ('item_code',code),('vat_amount',tax),('discount',discount),('gross_amount',amount if gross_column and not total_is_pretax else gross_word)] if w}
            ev['description']=[proof(w) for w in desc]
            if unit_word:ev['unit']=proof(unit_word)
            rtl_description=any(has_arabic(w['text']) for w in desc)
            items.append(dict(line_no=len(items)+1,item_code=normalize(code['text']) if code and (code.get('source')=='native_text' or code.get('confidence',0)>=85) else None,
                description=' '.join(w['text'] for w in sorted(desc,key=lambda w:(round(y(w)/h),-w['left'] if rtl_description else w['left']))),
                quantity=qty,unit=unit_word['text'] if unit_word else None,unit_price=pv,amount=av,vat_amount=tv,discount=dv,gross_amount=gross,
                amount_source='derived_quantity_price' if derived else 'printed' if av is not None else None,
                field_evidence=ev))
        if items:
            return items,header_y,h
    return [],None,h


def table_retry_reasons(words, rows, header_y, h):
    """Return source-layout reasons that justify an expensive cell reread."""
    if header_y is None or not rows:
        return ['table rows were not recovered']
    _,row_y=geometry(words)
    band=[w for w in words if abs(row_y(w)-header_y)<=h*3]
    reasons=[]
    for key in ('quantity','unit_price','amount'):
        if any(item.get(key) is None for item in rows):
            reasons.append(f'missing {key} in a detected row')
    optional={
        'item_code':'item_code', 'unit':'unit', 'vat_amount':'vat_amount',
        'discount':'discount', 'gross_amount':'gross_amount',
    }
    def has_distinct_header(key):
        matches=[w for w in band if header_match(w['text'],ALIASES[key])]
        if key=='item_code':
            matches=[w for w in matches if not contains(w['text'],('tax code','vat code','رمز الضريبة','كود الضريبة'))]
        if key=='unit':
            matches=[w for w in matches if not contains(w['text'],ALIASES['unit_price'])]
        if key=='vat_amount':
            matches=[w for w in matches if not contains(w['text'],('tax rate','vat rate','including vat','without vat','نسبة الضريبة'))]
        return bool(matches)
    for header_key,field in optional.items():
        if has_distinct_header(header_key) and any(item.get(field) is None for item in rows):
            reasons.append(f'missing {field} under a printed header')
    # A value selected for two semantic columns is positive evidence that the
    # header centre/tolerance was wrong, not that the source cell was blank.
    for index,item in enumerate(rows):
        used={}
        for key,value in item.get('field_evidence',{}).items():
            if key == 'description' or not isinstance(value,dict):
                continue
            bbox=tuple(round(float(v),2) for v in value.get('bbox',[]))
            if bbox in used:
                reasons.append(f'row {index+1} reuses one box for {used[bbox]} and {key}')
            used[bbox]=key
    return list(dict.fromkeys(reasons))


def parse_layout(pages,filename,language):
    if not pages: return None
    clean=[];receipts=[];items=[];headers=[]
    for i,page in enumerate(pages):
        pool,receipt=invoice_words(page)
        pool=[dict(w,_page=page.get('page',i+1)) for w in pool]
        if receipt: receipts.append(dict(page=page.get('page',i+1),bbox=receipt))
        found,hy,_=table(pool);headers.append(hy if hy is not None else header_hint(pool))
        for item in found:
            item['line_no']=len(items)+1;items.append(item)
        clean.append(pool)
    if not items and not any(h is not None for h in headers): return None
    words=clean[0];h,y=geometry(words)
    first_header=headers[0] if headers[0] is not None else float('inf')
    words=sorted([w for w in words if y(w)<first_header],key=lambda w:(y(w),w['left']))
    evidence={}
    def keep(path,word,value=None):
        if word is not None: evidence[path]=proof(word)
        return value if value is not None else word['text'] if word else None
    def near(label,predicate,pool=words,below=3):
        if label is None:return None
        candidates=[w for w in pool if w is not label and predicate(w['text']) and
                    (abs(y(w)-y(label))<=h*.95 or 0<y(w)-y(label)<=h*below and abs(w['left']-label['left'])<h*3)]
        return min(candidates,key=lambda w:(float(w.get('height',h))>h*1.8,abs(y(w)-y(label))>h*.95,
                                            abs(y(w)-y(label))*4+abs(w['left']-label['left']),w['text']),default=None)
    inv=None;date=None;time=None;supply_date=None
    for w in words:
        if w.get('retry_kind')=='invoice_identifier':
            inv=keep('invoice.invoice_number',w,normalize(w['text']));break
        if contains(w['text'],INVOICE_LABELS):
            m=re.search(r'(?:[:#]\s*|\b)([A-Za-z]+[-/][A-Za-z0-9/-]*\d[A-Za-z0-9/-]*|\d{3,})\s*$',normalize(w['text']))
            value=near(w,lambda s:bool(re.fullmatch(r'[A-Za-z0-9/-]*\d[A-Za-z0-9/-]*',normalize(s).lstrip(':# '))) and number_string(s,{15}) is None and not re.fullmatch(r'\d{1,4}[-/]\d{1,2}[-/]\d{2,4}',normalize(s)))
            if m: inv=keep('invoice.invoice_number',w,m[1]);break
            if value: inv=keep('invoice.invoice_number',value,normalize(value['text']).lstrip(':# '));break
    pattern=r'\b(?:\d{1,2}[-/]\d{1,2}[-/]20\d{2}|20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b'
    for w in words:
        if w.get('retry_kind')=='date':
            date=keep('invoice.date',w,w['text'])
            time=w.get('raw_time')
            break
        if contains(w['text'],DATE_LABELS) and not contains(w['text'],('due','delivery','supply date','date of supply','استحقاق','تسليم','توريد')):
            target=w if re.search(pattern,normalize(w['text'])) else near(w,lambda s:re.search(pattern,normalize(s)))
            if target:
                date=keep('invoice.date',target,re.search(pattern,normalize(target['text']))[0])
                match=re.search(r'\b\d{2}:\d{2}(?::\d{2})?\b',target['text'])
                if match:time=keep('invoice.time',target,match[0])
                if time is None:
                    time_word=near(target,lambda s:bool(re.search(r'\b\d{1,2}:\d{2}(?::\d{2})?\b',normalize(s))),below=2)
                    if time_word:
                        time=keep('invoice.time',time_word,re.search(r'\b\d{1,2}:\d{2}(?::\d{2})?\b',normalize(time_word['text']))[0])
                break
    for w in words:
        if not contains(w['text'],SUPPLY_DATE_LABELS):
            continue
        target=w if re.search(pattern,normalize(w['text'])) else near(
            w,lambda s:bool(re.search(pattern,normalize(s))),below=2)
        if target:
            supply_date=keep('invoice.date_of_supply',target,re.search(pattern,normalize(target['text']))[0])
            break
    buyer_candidates=[w for w in words if contains(w['text'],CUSTOMER_SECTION_LABELS)
                      and not contains(w['text'],('signature','seal','company','trading','est','establishment','vat','tax','الضربي','الضريبي','ختم','توقيع'))]
    def buyer_rank(w):
        if contains(w['text'],CUSTOMER_NAME_LABELS):return 0
        if contains(w['text'],('customer code','cus code','customer no','كود العميل','رقم العميل')):return 2
        return 1
    buyer=min(buyer_candidates,key=lambda w:(buyer_rank(w),y(w),w['left']),default=None)
    def name_text(s):
        normalized=normalize(s).strip(' :')
        label_only=normalized.casefold().replace('.',' ').strip() in {'customer','customer name','cust name','custname','buyer','buyer name'}
        joined_address=bool(re.search(r'(?i)(?:building|post\s*code|add(?:itional)?\s*no|short\s*adrs|المبنى|الرمز\s*البريدي|الرقم\s*الإضافي)\s*[:#-]?\s*\d',normalized))
        identifier_like=bool(number_string(normalized,{15}) or re.search(r'(?i)(?:tax\s*code|taxcode|vat\s*(?:no|number)|الرقم\s*الضريبي)',normalized))
        return (not label_only and not joined_address and not identifier_like and len(normalized)>8 and bool(re.search(r'[A-Za-z\u0600-\u06ff]',normalized)) and
                not contains(normalized,('invoice','date','vat','tax','building no','street','mobile','postal','number','email',
                    'customer details','customer code','cus code','customer no','cr no','commercial registration',
                    'تفاصيل العميل','تفاصيل العملاء','كود العميل','رقم العميل','السجل التجاري','الرقم الضريبي',
                    'رقم','التاريخ','عنوان','المبنى','الشارع','الحي','الرمز البريدي')))
    def customer_tail(s):
        value=re.sub(r'(?i)^(?:(?:customer|cust\.?|buyer)\s*)?name\s*[:：]?\s*','',s).strip(' :')
        return re.sub(r'^(?:اسم\s+(?:العميل|المشتري|الزبون))\s*[:：]?\s*','',value).strip(' :')
    buyer_name=None
    if buyer:
        explicit=next((w for w in words if y(buyer)-h<y(w)<first_header and
                       (re.match(r'(?i)^(?:customer\s+)?name\s*:',w['text']) or contains(w['text'],CUSTOMER_NAME_LABELS)) and
                       name_text(customer_tail(w['text']))),None)
        nearby=[w for w in words if w is not buyer and y(buyer)-h<y(w)<min(first_header,y(buyer)+h*6) and name_text(w['text'])]
        company_like=[w for w in nearby if contains(w['text'],('company','trading','establishment','contracting','شركة','مؤسسة','مؤسسه','مقاولات'))]
        buyer_name=explicit or min(company_like or nearby,key=lambda w:(
            w.get('retry_kind')!='customer_name_ar',abs(y(w)-y(buyer)),
            abs(center(w)[0]-center(buyer)[0])),default=None)
    customer_value=None
    if buyer_name:
        customer_value=customer_tail(buyer_name['text'])
    customer_name=keep('customer.name',buyer_name,customer_value)
    if buyer_name and buyer_name.get('source')!='native_text' and buyer_name.get('confidence',0)<80:
        customer_name=None
    vats=[(w,number_string(w['text'],{15})) for w in words];vats=[(w,v) for w,v in vats if v]
    sv=next(((w,v) for w,v in vats if not buyer or y(w)<y(buyer)),(None,None))
    explicit_customer=next(((w,v) for w,v in vats if contains(w['text'],CUSTOMER_VAT_LABELS)),None)
    explicit_supplier=next(((w,v) for w,v in vats if contains(w['text'],SUPPLIER_VAT_LABELS) and not contains(w['text'],CUSTOMER_VAT_LABELS)),None)
    if explicit_supplier:sv=explicit_supplier
    customer_vat_label=next((w for w in words if contains(w['text'],CUSTOMER_VAT_LABELS) or
                             ('عميل' in w['text'] and any(s in w['text'] for s in ('الضري','الضرب')))),None)
    customer_vat_word=near(customer_vat_label,lambda s:number_string(s,{15}) is not None,below=3)
    if customer_vat_word is None and customer_vat_label:
        customer_vat_word=min((w for w,v in vats if abs(y(w)-y(customer_vat_label))<h*2 and v!=sv[1]),key=lambda w:abs(y(w)-y(customer_vat_label)),default=None)
    cv=(customer_vat_word,number_string(customer_vat_word['text'],{15})) if customer_vat_word else next(((w,v) for w,v in vats if buyer and y(w)>y(buyer) and v!=sv[1]),(None,None))
    if explicit_customer:cv=explicit_customer
    supplier_vat=keep('supplier.vat_number',*sv);customer_vat=keep('customer.vat_number',*cv)
    customer_address=None
    if buyer:
        customer_address,address_proofs=section_address(words,y(buyer),first_header,h,y)
        if address_proofs:evidence['customer.address']=address_proofs
    header=[w for w in words if (not buyer or y(w)<y(buyer)) and name_text(w['text']) and
            not contains(w['text'],('for industrial','للمواد','للتجارة في','since'))]
    def company(arabic):
        def is_arabic(s):
            return len(re.findall(r'[\u0600-\u06ff]',s))>len(re.findall('[A-Za-z]',s))
        candidates=[w for w in header if is_arabic(w['text'])==arabic and
                    (contains(w['text'],('شركة','مؤسسة','مؤسسه','company','trading','est','establishment','trad','materials','building')) or
                     not arabic and len(re.findall('[A-Z]',w['text']))>15)]
        retry_kind='supplier_name_ar' if arabic else 'supplier_name_en'
        w=min(candidates,key=lambda w:(w.get('retry_kind')!=retry_kind,y(w),-w['width']),default=None)
        return keep('supplier.name_ar' if arabic else 'supplier.name_en',w)
    name_ar,name_en=company(True),company(False)
    pay_label=next((w for w in words if contains(w['text'],('payment method','payment mthd','payment methd','payment type','طريقة الدفع','نوع الدفع'))),None)
    pay=next((w for w in words if contains(w['text'],('cash','card','credit','mada','span','network','بالنقد','نقدي','بطاقة','مدى'))),None)
    if pay_label:
        pay=pay_label if any(token in normalize(pay_label['text']).casefold() for token in ('cash','card','credit','mada','span','بالنقد','نقدي','بطاقة','مدى')) else near(
            pay_label,lambda s:contains(s,('cash','card','credit','mada','span','network','بالنقد','نقدي','بطاقة','مدى')),below=2)
    payment=None
    if pay:
        raw_payment=normalize(pay['text']).strip(' :')
        raw_payment=re.sub(r'(?i)^(?:payment\s+(?:method|mthd|methd|type)|طريقة\s+الدفع|نوع\s+الدفع)\s*[:：]?\s*','',raw_payment).strip(' :')
        payment=raw_payment or None
    payment=keep('invoice.payment_method',pay,payment)
    footer=clean[-1];fh,fy=geometry(footer)
    last_header=headers[-1] or 0
    item_bottom=max((e['bbox'][1]+e['bbox'][3] for item in items for e in item['field_evidence'].values() if isinstance(e,dict) and e['page']==pages[-1].get('page',len(pages))),default=last_header)
    footer=[w for w in footer if w['top']>item_bottom]
    def total(path,aliases):
        matches=[]
        for label in footer:
            if not contains(label['text'],aliases) and not (path=='totals.vat_amount' and re.fullmatch(r'(?i)VAT\s+\d+(?:\.\d+)?%',label['text'].strip())):continue
            if path=='totals.subtotal' and contains(label['text'],('amount due','net','grand')):continue
            if path=='totals.subtotal' and contains(label['text'],('total vat','vat amount','tax amount','المجموع الضريبة')):continue
            if path=='totals.vat_amount' and contains(label['text'],('without','excl','with vat','بدون','مع الضريبة','شامل')):continue
            candidates=[w for w in footer if w is not label and numeric(w['text']) is not None and w.get('height',fh)>=fh*.5 and abs(fy(w)-fy(label))<=fh*1.2 and (path!='totals.discount' or abs(center(w)[0]-center(label)[0])<fh*12)]
            value=min(candidates,key=lambda w:(abs(fy(w)-fy(label)),abs(center(w)[0]-center(label)[0])),default=None)
            if value:
                number=numeric(value['text'])
                if path=='totals.subtotal' and number==0 and any((item.get('amount') or 0)>0 for item in items):
                    continue
                specificity=max((len(re.findall(r'[^\W_]+',alias)) for alias in aliases if contains(label['text'],(alias,))),default=1)
                matches.append((specificity,fy(label),label,value,number))
        if not matches:return None
        _,_,_,value,number=max(matches,key=lambda match:(match[0],match[1]))
        return keep(path,value,number)
    subtotal=total('totals.subtotal',('subtotal','gross amount','total amount','total excluding vat','total excl vat','total amt excluding vat','total (excl) vat','before tax',
        'taxable total','taxable value','total taxable amount','total taxble amount excluding vat','total taxable amount excluding vat',
        'الإجمالي بدون الضريبة','الإجمالي قبل الضريبة','المجموع قبل الضريبة',
        'إجمالي المبلغ غير شامل الضريبة','إجمالي المبلغ الخاضع للضريبة','إجمالي المبلغ الخاضع','القيمة الخاضعة','المجموع','الجموع'))
    vat=total('totals.vat_amount',('total vat','vat amount','tax amount','total tax','ضريبة القيمة','ضريية القيمة','الضريبة','الضرية',
        'إجمالي ضريبة القيمة المضافة','مجموع ضريبة القيمة المضافة','إجمالي الضريبة'))
    net=total('totals.net_amount',('grand total','invoice total','amount due','net amount','net total','total with vat','total including vat','total amt including vat','including vat',
        'after tax','الإجمالي بما','قيمة الفاتورة مع الضريبة','المبلغ المستحق','إجمالي الفاتورة','الإجمالي شامل','الإجمالي بعد الضريبة',
        'إجمالي المبلغ شامل الضريبة','إجمالي المبلغ المستحق'))
    # A shared TOTAL row places net and VAT under their respective table columns.
    column_words=clean[-1]
    for label in footer:
        if normalize(label['text']).strip(' :').casefold() not in {'total','مجموع'}:continue
        for key,aliases in [('subtotal',('amount',)),('vat_amount',('vat',))]:
            heading=min((w for w in column_words if abs(fy(w)-last_header)<fh*3 and contains(w['text'],aliases) and not contains(w['text'],('vat no','vat number'))),key=lambda w:abs(fy(w)-last_header),default=None)
            if heading:
                values=[w for w in footer if numeric(w['text']) is not None and abs(fy(w)-fy(label))<fh*1.2 and abs(center(w)[0]-center(heading)[0])<fh*3]
                value=min(values,key=lambda w:abs(center(w)[0]-center(heading)[0]),default=None)
                if value:
                    if key=='subtotal' and subtotal is None:subtotal=keep('totals.subtotal',value,numeric(value['text']))
                    if key=='vat_amount' and vat is None:vat=keep('totals.vat_amount',value,numeric(value['text']))
    rates=[(w,float(m[1])) for w in clean[-1] for m in re.finditer(r'(\d+(?:\.\d+)?)\s*%',normalize(w['text'])) if contains(w['text'],('vat','tax')) or re.fullmatch(r'\s*\d+(?:\.\d+)?\s*%\s*',normalize(w['text']))]
    rate=keep('totals.vat_rate',rates[0][0],rates[0][1]) if rates and len({v for _,v in rates})==1 else None
    text=' '.join(w['text'] for w in clean[-1])
    currency=next((c for c in ('SAR','USD','AED','EUR','GBP','PKR') if re.search(r'\b'+c+r'\b',text)),None)
    if currency is None and re.search(r'Saudi\s*Riyal|ر?يال\s+سعودي',text,re.I):currency='SAR'
    return dict(document_type='invoice',document_language=language.split('+'),source_filename=filename,
                supplier=dict(name_ar=name_ar,name_en=name_en,vat_number=supplier_vat),
                invoice=dict(invoice_number=inv,date=date,date_of_supply=supply_date,hijri_date=None,time=time,payment_method=payment),
                customer=dict(name=customer_name,vat_number=customer_vat,address=customer_address),items=items,
                totals=dict(subtotal=subtotal,discount=total('totals.discount',('discount','discounts','dscounts','خصم','الخصومات','الحسم')),vat_rate=rate,vat_amount=vat,net_amount=net,currency=currency),
                field_evidence=evidence,receipt_regions=receipts)
