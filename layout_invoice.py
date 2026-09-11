"""Invoice fields from page-local, skew-aware columns and label/value evidence."""
from __future__ import annotations
import re
from decimal import Decimal
from statistics import median
from invoice_formatter import DIGIT_TABLE, center, contains, has_arabic, normalize, number_string
from document_regions import invoice_words

ALIASES = {
    'description': ('item description','description','product','وصف','البيان','اسم الصنف'),
    'quantity': ('quantity','qty','الكمية','كمية'),
    'unit_price': ('unit price','rate','price','السعر','سعر الوحدة','سعر افرادي','سعر أفرادي'),
    'amount': ('taxable value','taxable','line amount','net amount','amount','total','القيمة الخاضعة','الاجمالي','الإجمالي'),
    'item_code': ('item code','item no','sku','product code','رمز الصنف','رقم الصنف','كود الصنف'),
    'serial': ('s no','sn','الرقم','مسلسل'),
    'unit': ('uom','unit','الوحدة'),
    'vat_amount': ('vat amount','tax amount','vat','الضريبة','قيمة الضريبة','قيمة الضريبية'),
    'vat_rate': ('tax rate',),
    'discount': ('discount','خصم'),
    'gross_amount': ('item subtotal','including vat'),
}


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
    separated=re.sub(r'(?<=\S)(الوحدة|الكمية|الضريبة)',r' \1',text)
    return contains(separated,aliases)


def proof(word):
    if word is None:
        return None
    return dict(page=word.get('_page',1), text=word['text'], confidence=word.get('confidence'),
                source=word.get('source','ocr'), bbox=[word.get(k,0) for k in ('left','top','width','height')])


def header_hint(words):
    """Locate a likely header without requiring successfully parsed item rows."""
    h,y=geometry(words)
    for w in sorted(words,key=lambda w:(y(w),w.get('left',0))):
        if contains(w['text'],ALIASES['description']):
            peers=[p for p in words if abs(y(p)-y(w))<h*3 and
                   any(contains(p['text'],ALIASES[k]) for k in ('item_code','quantity','amount'))]
            if peers:return y(w)
    return None


def table(words):
    h,y=geometry(words)
    words=sorted(words,key=lambda w:(y(w),w.get('left',0),w['text']))
    for q in words:
        if not contains(q['text'],ALIASES['quantity']):
            continue
        band=[w for w in words if abs(y(w)-y(q))<=3*h]
        headers={}
        for key,aliases in ALIASES.items():
            choices=[w for w in band if header_match(w['text'],aliases)]
            if key=='unit':
                choices=[w for w in choices if not contains(w['text'],ALIASES['unit_price'])]
            if key=='unit_price':
                choices=[w for w in choices if not contains(w['text'],('tax rate',))]
            if key=='vat_amount':
                choices=[w for w in choices if not contains(w['text'],('tax rate','without vat','including vat'))]
            if key=='amount':
                choices=[w for w in choices if not contains(w['text'],('vat amount','tax amount','total with vat','total (excl) vat'))]
            headers[key]=min(choices,key=lambda w:(
                not contains(w['text'],('taxable','القيمة الخاضعة')) if key=='amount' else False,
                not contains(w['text'],('قيمة','amount')) if key=='vat_amount' else False,
                not bool(re.search('[A-Za-z]',w['text'])),
                abs(y(w)-y(q)),w['left']),default=None)
        if not all(headers[k] for k in ('description','quantity','unit_price','amount')):
            continue
        hx={k:center(w)[0] for k,w in headers.items() if w}
        if len({hx[k] for k in ('description','quantity','unit_price','amount')})<4:
            continue
        for key in ('vat_amount','unit','serial','item_code','discount','vat_rate','gross_amount'):
            if key in hx and any(hx[key]==hx[k] for k in ('description','quantity','unit_price','amount')):
                hx.pop(key)
        header_y=max(y(headers[k]) for k in ('description','quantity','unit_price','amount'))
        stop=min((y(w) for w in words if y(w)>header_y+2*h and contains(w['text'],
                 ('subtotal','grand total','gross amount','total amount','total excluding vat',
                  'taxable total','total vat','total (excl) vat','الإجمالي بدون الضريبة','amount chargeable','declaration','الإفصاح','إجمالي الفاتورة'))),default=float('inf'))
        body=[w for w in words if header_y+h*.6<y(w)<stop]
        def tolerance(key):
            return max(h*.75,min(abs(hx[key]-x) for k,x in hx.items() if k!=key)*.58)
        anchors=[w for w in body if numeric(w['text']) is not None and abs(center(w)[0]-hx['quantity'])<=tolerance('quantity')]
        anchors.sort(key=lambda w:(y(w),abs(center(w)[0]-hx['quantity'])))
        unique=[]
        for w in anchors:
            if not unique or abs(y(w)-y(unique[-1]))>h*.7:
                unique.append(w)
        quantity_missing = not unique
        if quantity_missing:
            # A faint quantity must not erase an otherwise visible item row.
            for w in body:
                if numeric(w['text']) is None and abs(center(w)[0]-hx['description'])<tolerance('description') and not contains(w['text'],('total','vat','discount')):
                    if not unique or abs(y(w)-y(unique[-1]))>h*.7:
                        unique.append(w)
        items=[]
        exclusive=any(contains(w['text'],('taxable','without vat','القيمة الخاضعة')) and
                      abs(center(w)[0]-hx['amount'])<h*3 for w in band)
        gross_column=not exclusive and 'vat_amount' in hx and contains(headers['amount']['text'],('total amount','الاجمالي','الإجمالي'))
        for i,anchor in enumerate(unique):
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
            code_candidates=[w for w in row if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/-]{2,}',normalize(w['text'])) and ('item_code' in hx or re.search(r'\d',w['text']))]
            if 'item_code' in hx:
                code=min((w for w in code_candidates if abs(center(w)[0]-hx['item_code'])<=tolerance('item_code')),
                         key=lambda w:abs(center(w)[0]-hx['item_code']),default=None)
            else:
                code=min((w for w in code_candidates if w['left']<headers['description']['left'] and
                          center(w)[0]>hx.get('serial',-float('inf'))+h*.5),key=lambda w:w['left'],default=None)
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
            if not desc or (price is None and amount is None):
                continue
            qty=None if quantity_missing else numeric(anchor['text']);pv=numeric(price['text']) if price else None
            if quantity_missing and price and price.get('source')=='targeted_ocr':
                pv=None  # A corrected faint price cannot be reconciled without quantity.
            av=numeric(amount['text']) if amount else None
            tv=numeric(tax['text']) if tax else None
            dv=numeric(discount['text']) if discount else None
            gross_word=cell('gross_amount')
            derived=False;gross=av if gross_column else numeric(gross_word['text']) if gross_word else None
            if gross_column:
                av=None
                if qty is not None and pv is not None and tv is not None and gross is not None:
                    calculated=Decimal(str(qty))*Decimal(str(pv))-Decimal(str(dv or 0))
                    if abs(calculated+Decimal(str(tv))-Decimal(str(gross)))<=Decimal('.02'):
                        av=float(calculated);derived=True
            ev={k:proof(w) for k,w in [('quantity',None if quantity_missing else anchor),('unit_price',price),('amount',None if gross_column else amount),
                                      ('item_code',code),('vat_amount',tax),('discount',discount),('gross_amount',amount if gross_column else gross_word)] if w}
            ev['description']=[proof(w) for w in desc]
            items.append(dict(line_no=len(items)+1,item_code=normalize(code['text']) if code and (code.get('source')=='native_text' or code.get('confidence',0)>=85) else None,
                description=' '.join(w['text'] for w in sorted(desc,key=lambda w:(round(y(w)/h),w['left']))),
                quantity=qty,unit_price=pv,amount=av,vat_amount=tv,discount=dv,gross_amount=gross,
                amount_source='derived_quantity_price' if derived else 'printed' if av is not None else None,
                field_evidence=ev))
        if items:
            return items,header_y,h
    return [],None,h


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
        return min(candidates,key=lambda w:(abs(y(w)-y(label))>h*.95,abs(y(w)-y(label))*4+abs(w['left']-label['left']),w['text']),default=None)
    inv=None;date=None;time=None
    for w in words:
        if w.get('retry_kind')=='invoice_identifier':
            inv=keep('invoice.invoice_number',w,normalize(w['text']));break
        if contains(w['text'],('invoice no','invoice number','inv no','رقم الفاتورة')):
            m=re.search(r'(?:[:#]\s*|\b)([A-Za-z]+[-/][A-Za-z0-9/-]*\d[A-Za-z0-9/-]*|\d{3,})\s*$',normalize(w['text']))
            value=near(w,lambda s:bool(re.fullmatch(r'[A-Za-z0-9/-]*\d[A-Za-z0-9/-]*',normalize(s))) and number_string(s,{15}) is None and not re.fullmatch(r'\d{1,4}[-/]\d{1,2}[-/]\d{2,4}',normalize(s)))
            if m: inv=keep('invoice.invoice_number',w,m[1]);break
            if value: inv=keep('invoice.invoice_number',value,normalize(value['text']));break
    pattern=r'\b(?:\d{1,2}[-/]\d{1,2}[-/]20\d{2}|20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b'
    for w in words:
        if w.get('retry_kind')=='date':
            date=keep('invoice.date',w,w['text'])
            time=w.get('raw_time')
            break
        if contains(w['text'],('date','dated','التاريخ')) and not contains(w['text'],('due','delivery','استحقاق')):
            target=w if re.search(pattern,normalize(w['text'])) else near(w,lambda s:re.search(pattern,normalize(s)))
            if target:
                date=keep('invoice.date',target,re.search(pattern,normalize(target['text']))[0])
                match=re.search(r'\b\d{2}:\d{2}(?::\d{2})?\b',target['text'])
                if match:time=keep('invoice.time',target,match[0])
                break
    buyer=next((w for w in words if contains(w['text'],('buyer','bill to','customer','المشتري','العميل','السادة'))
                and not contains(w['text'],('signature','seal','customer no','company','trading','est','establishment','vat','الضربي','الضريبي','ختم','توقيع'))),None)
    def name_text(s):
        return len(s)>8 and not contains(s,('invoice','date','vat','building no','street','mobile','postal','number','email','رقم','التاريخ','عنوان'))
    buyer_name=None
    if buyer:
        explicit=next((w for w in words if y(buyer)<y(w)<first_header and re.match(r'(?i)^name\s*:',w['text'])),None)
        buyer_name=explicit or near(buyer,name_text,below=4)
    customer_name=keep('customer.name',buyer_name,re.sub(r'(?i)^name\s*:\s*','',buyer_name['text']) if buyer_name else None)
    if buyer_name and buyer_name.get('source')!='native_text' and buyer_name.get('confidence',0)<80:
        customer_name=None
    vats=[(w,number_string(w['text'],{15})) for w in words];vats=[(w,v) for w,v in vats if v]
    sv=next(((w,v) for w,v in vats if not buyer or y(w)<y(buyer)),(None,None))
    customer_vat_label=next((w for w in words if 'عميل' in w['text'] and any(s in w['text'] for s in ('الضري','الضرب'))),None)
    customer_vat_word=near(customer_vat_label,lambda s:number_string(s,{15}) is not None,below=3)
    if customer_vat_word is None and customer_vat_label:
        customer_vat_word=min((w for w,v in vats if abs(y(w)-y(customer_vat_label))<h*2 and v!=sv[1]),key=lambda w:abs(y(w)-y(customer_vat_label)),default=None)
    cv=(customer_vat_word,number_string(customer_vat_word['text'],{15})) if customer_vat_word else next(((w,v) for w,v in vats if buyer and y(w)>y(buyer) and v!=sv[1]),(None,None))
    supplier_vat=keep('supplier.vat_number',*sv);customer_vat=keep('customer.vat_number',*cv)
    header=[w for w in words if (not buyer or y(w)<y(buyer)) and name_text(w['text']) and
            not contains(w['text'],('for industrial','للمواد','للتجارة في','since'))]
    def company(arabic):
        def is_arabic(s):
            return len(re.findall(r'[\u0600-\u06ff]',s))>len(re.findall('[A-Za-z]',s))
        candidates=[w for w in header if is_arabic(w['text'])==arabic and
                    (contains(w['text'],('شركة','مؤسسة','مؤسسه','company','trading','est','establishment','trad','materials','building')) or
                     not arabic and len(re.findall('[A-Z]',w['text']))>15)]
        w=min(candidates,key=lambda w:(y(w),-w['width']),default=None)
        return keep('supplier.name_ar' if arabic else 'supplier.name_en',w)
    name_ar,name_en=company(True),company(False)
    pay=next((w for w in words if contains(w['text'],('cash','card','credit','بالنقد'))),None)
    payment=next((v for v in ('cash','card','credit') if pay and contains(pay['text'],(v,))), 'cash' if pay and 'بالنقد' in pay['text'] else None)
    payment=keep('invoice.payment_method',pay,payment.title() if payment else None)
    footer=clean[-1];fh,fy=geometry(footer)
    last_header=headers[-1] or 0
    item_bottom=max((e['bbox'][1]+e['bbox'][3] for item in items for e in item['field_evidence'].values() if isinstance(e,dict) and e['page']==pages[-1].get('page',len(pages))),default=last_header)
    footer=[w for w in footer if w['top']>item_bottom]
    def total(path,aliases):
        for label in sorted(footer,key=lambda w:(fy(w),w['left']),reverse=True):
            if not contains(label['text'],aliases) and not (path=='totals.vat_amount' and re.fullmatch(r'(?i)VAT\s+\d+(?:\.\d+)?%',label['text'].strip())):continue
            if path=='totals.subtotal' and contains(label['text'],('amount due','net','grand')):continue
            if path=='totals.subtotal' and contains(label['text'],('total vat','vat amount','tax amount','المجموع الضريبة')):continue
            if path=='totals.vat_amount' and contains(label['text'],('without','excl','with vat','بدون','مع الضريبة','شامل')):continue
            candidates=[w for w in footer if w is not label and numeric(w['text']) is not None and w.get('height',fh)>=fh*.5 and abs(fy(w)-fy(label))<=fh*1.2 and (path!='totals.discount' or abs(center(w)[0]-center(label)[0])<fh*12)]
            value=min(candidates,key=lambda w:(abs(fy(w)-fy(label)),abs(center(w)[0]-center(label)[0])),default=None)
            if value:return keep(path,value,numeric(value['text']))
        return None
    subtotal=total('totals.subtotal',('subtotal','gross amount','total amount','total excluding vat','total (excl) vat','الإجمالي بدون الضريبة','taxable total','taxable value','القيمة الخاضعة','المجموع','الجموع'))
    vat=total('totals.vat_amount',('total vat','vat amount','tax amount','ضريبة القيمة','ضريية القيمة','الضريبة','الضرية'))
    net=total('totals.net_amount',('grand total','invoice total','amount due','net amount','net total','total with vat','قيمة الفاتورة مع الضريبة','المبلغ المستحق','إجمالي الفاتورة','الإجمالي شامل'))
    rates=[(w,float(m[1])) for w in clean[-1] for m in re.finditer(r'(\d+(?:\.\d+)?)\s*%',normalize(w['text'])) if contains(w['text'],('vat','tax')) or re.fullmatch(r'\s*\d+(?:\.\d+)?\s*%\s*',normalize(w['text']))]
    rate=keep('totals.vat_rate',rates[0][0],rates[0][1]) if rates and len({v for _,v in rates})==1 else None
    text=' '.join(w['text'] for w in clean[-1])
    currency=next((c for c in ('SAR','USD','AED','EUR','GBP','PKR') if re.search(r'\b'+c+r'\b',text)),None)
    if currency is None and re.search(r'Saudi\s*Riyal|ر?يال\s+سعودي',text,re.I):currency='SAR'
    return dict(document_type='invoice',document_language=language.split('+'),source_filename=filename,
                supplier=dict(name_ar=name_ar,name_en=name_en,vat_number=supplier_vat),
                invoice=dict(invoice_number=inv,date=date,hijri_date=None,time=time,payment_method=payment),
                customer=dict(name=customer_name,vat_number=customer_vat,address=None),items=items,
                totals=dict(subtotal=subtotal,discount=total('totals.discount',('discount','discounts','dscounts','خصم','الخصومات','الحسم')),vat_rate=rate,vat_amount=vat,net_amount=net,currency=currency),
                field_evidence=evidence,receipt_regions=receipts)
