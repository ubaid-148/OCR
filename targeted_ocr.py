"""Bounded region re-OCR with page-coordinate provenance and retained alternatives."""
import re
from pathlib import Path
from statistics import median
from invoice_formatter import contains, center, has_arabic, normalize
from document_regions import invoice_words
from layout_invoice import (
    ALIASES, CUSTOMER_SECTION_LABELS, DATE_LABELS, INVOICE_LABELS,
    address_label, customer_name_end, geometry, header_hint, header_match, numeric, parse_layout, table,
    table_retry_reasons,
)
from page_rotation import clamp_crop_bbox, render_upright_page, scale_bbox


def _missing_row_reasons(parsed, rows):
    """Use printed totals only to detect omitted rows, never to invent values."""
    if not parsed or not rows:
        return []
    totals=parsed.get('totals',{})
    effective={'subtotal':[],'vat_amount':[],'net_amount':[]}
    for item in rows:
        qty=numeric(str(item.get('quantity'))) if item.get('quantity') is not None else None
        amount=numeric(str(item.get('amount'))) if item.get('amount') is not None else None
        vat=numeric(str(item.get('vat_amount'))) if item.get('vat_amount') is not None else None
        gross=numeric(str(item.get('gross_amount'))) if item.get('gross_amount') is not None else None
        discount=numeric(str(item.get('discount'))) if item.get('discount') is not None else 0
        per_unit=(qty is not None and amount is not None and vat is not None and gross is not None and
                  abs(qty*(amount+vat)-discount-gross)<=.03)
        effective['subtotal'].append(qty*amount-discount if per_unit else amount)
        effective['vat_amount'].append(qty*vat if per_unit else vat)
        effective['net_amount'].append(gross)
    reasons=[]
    for field,label in (('subtotal','subtotal'),('vat_amount','VAT'),('net_amount','net total')):
        printed=totals.get(field)
        values=effective[field]
        if printed is not None and values and all(value is not None for value in values):
            recovered=sum(values)
            if float(printed)>recovered+.03:
                reasons.append(f'printed {label} exceeds recovered item rows')
    return reasons


def plan_regions(page):
    words,_=invoice_words(page)
    h=sorted([w.get('height',20) for w in words])[len(words)//2] if words else 20
    rows,header,_=table(words)
    hint=header_hint(words)
    if header is None:
        header=hint
    page_width=page.get('canonical_width',page.get('width',1)*page.get('render_dpi',200)/72)
    page_height=page.get('canonical_height',page.get('height',1)*page.get('render_dpi',200)/72)
    header_limit=header if header is not None else page_height*.5
    candidates=[]
    parsed=parse_layout([page],'','')
    totals=parsed.get('totals',{}) if parsed else {}
    # Missing totals alone get a footer crop. A positive gap between printed
    # totals and complete rows also warrants one bounded search for omitted rows.
    table_issues=table_retry_reasons(words,rows,header,h)
    table_issues.extend(reason for reason in _missing_row_reasons(parsed,rows)
                        if reason not in table_issues)
    incomplete_table=bool(table_issues)
    known_invoice=parsed and parsed['invoice'].get('invoice_number')
    known_invoice_evidence=parsed.get('field_evidence',{}).get('invoice.invoice_number') if parsed else None
    known_vats=bool(parsed) and all(
        re.fullmatch(r'\d{15}',parsed[party].get('vat_number') or '') and
        (parsed.get('field_evidence',{}).get(f'{party}.vat_number',{}).get('source')=='native_text' or
         float(parsed.get('field_evidence',{}).get(f'{party}.vat_number',{}).get('confidence') or 0)>=85)
        for party in ('supplier','customer'))
    if known_invoice_evidence and known_invoice_evidence.get('bbox'):
        # Oversized text inside a form field is often a handwritten note. Re-read
        # the row so a smaller printed serial beside either bilingual label can win.
        known_invoice=known_invoice and known_invoice_evidence['bbox'][3]<=h*1.8
    supplier_ar_evidence=parsed.get('field_evidence',{}).get('supplier.name_ar',{}) if parsed else {}
    if parsed and (not parsed['supplier'].get('name_ar') or
                   supplier_ar_evidence and supplier_ar_evidence.get('source')!='native_text' and
                   float(supplier_ar_evidence.get('confidence') or 0)<80):
        name_anchor=next((w for w in words if contains(w['text'],('مؤسسة','مؤسسه','شركة')) and center(w)[1]<header_limit*.5),None)
        if name_anchor:
            cluster=[w for w in words if has_arabic(w['text']) and abs(center(w)[1]-center(name_anchor)[1])<h]
            candidates.append(dict(kind='supplier_name_ar',original=name_anchor,language='ar',bbox=[min(w['left'] for w in cluster)-h*.2,min(w['top'] for w in cluster)-h*.2,max(w['left']+w['width'] for w in cluster)+h*.2,max(w['top']+w['height'] for w in cluster)+h*.2]))
    def add(w,kind,part='whole'):
        x0,y0=w['left'],w['top'];x1=x0+w['width'];y1=y0+w['height']
        if part=='left':x1=x0+w['width']*.62
        if part=='right':x0=x0+w['width']*.38
        if part=='identifier':x0=x0+w['width']*.60
        candidates.append(dict(bbox=[x0-h*.2,y0-h*.25,x1+h*.2,y1+h*.25],kind=kind,original=w))
    def add_row(w,kind,language='en'):
        candidates.append(dict(bbox=[0,w['top']-h*.6,page_width,w['top']+w['height']+h*.6],
                               kind=kind,original=w,language=language))
    supplier_en=parsed.get('supplier',{}).get('name_en') if parsed else None
    compact_supplier=re.sub(r'[^A-Za-z]','',supplier_en or '')
    bad_supplier_en=(not supplier_en or
                     bool(re.search(r'[a-z][A-Z]',supplier_en or '')) or
                     (len(compact_supplier)>18 and len((supplier_en or '').split())<3))
    if parsed and bad_supplier_en:
        name_anchor=next((w for w in words if contains(w['text'],('مؤسسة','مؤسسه','شركة')) and center(w)[1]<header_limit*.5),None)
        if name_anchor:
            cluster=[w for w in words if abs(center(w)[1]-center(name_anchor)[1])<h]
            candidates.append(dict(kind='supplier_name_en',original=name_anchor,language='en',
                bbox=[max(0,min(w['left'] for w in cluster)-h),max(0,min(w['top'] for w in cluster)-h*.4),
                      min(page_width,max(w['left']+w['width'] for w in cluster)+h),
                      min(page_height,max(w['top']+w['height'] for w in cluster)+h*4)]))
    customer_value=parsed.get('customer',{}).get('name') if parsed else None
    customer_evidence=parsed.get('field_evidence',{}).get('customer.name',{}) if parsed else {}
    bad_customer=(not customer_value or contains(customer_value,CUSTOMER_SECTION_LABELS) or
                  has_arabic(customer_value) and len(customer_value.split())<3 and
                  contains(customer_value,('مؤسسة','مؤسسه','شركة')) or
                  address_label(customer_value) or
                  re.search(r'(?i)(?:building|post\s*code|add\s*no|المبنى|الرمز\s*البريدي)\s*\d',customer_value) or
                  customer_evidence and customer_evidence.get('source')!='native_text' and
                  float(customer_evidence.get('confidence') or 0)<80)
    if bad_customer:
        customer_anchor=next((w for w in words if contains(w['text'],CUSTOMER_SECTION_LABELS) and
                              not contains(w['text'],('customer code','cus code','customer no','vat','tax','كود العميل','رقم العميل','الضريبي'))),None)
        if customer_anchor:
            _,row_y=geometry(words)
            name_end=customer_name_end(words,customer_anchor,h,row_y,header_limit)
            labels=[w for w in words if abs(center(w)[1]-center(customer_anchor)[1])<h*1.5 and
                    contains(w['text'],CUSTOMER_SECTION_LABELS) and
                    not contains(w['text'],('vat','tax','الضريبي'))]
            left=max((w['left']+w['width'] for w in labels if re.search('[A-Za-z]',w['text'])),default=page_width*.42)
            right=min((w['left'] for w in labels if has_arabic(w['text']) and w['left']>left),default=page_width)
            if right-left>h*4:
                candidates.append(dict(kind='customer_name_ar',original=customer_anchor,language='ar',
                    dpi=400,enhance=True,bbox=[left+h*.15,min(w['top'] for w in labels)-h*.35,
                                             right-h*.15,max(w['top']+w['height'] for w in labels)+h*.35]))
            else:
                add_row(customer_anchor,'customer_name_ar','ar')
                candidates[-1].update(dpi=400, enhance=True)
            # Many bilingual forms place the value immediately left of the
            # combined "Customer / العميل" label instead of between two labels.
            label_left=min(w['left'] for w in labels)
            value_left=max(0,label_left-page_width*.48)
            value_right=label_left-h*.15
            if value_right-value_left>h*4:
                candidates.append(dict(kind='customer_name_ar',original=customer_anchor,language='ar',
                    dpi=400,enhance=True,bbox=[value_left,min(w['top'] for w in labels)-h*.35,
                                             value_right,max(w['top']+w['height'] for w in labels)+h*.35]))
            for region in candidates:
                if region['kind']=='customer_name_ar':
                    region['bbox'][3]=min(region['bbox'][3],
                        name_end+center(customer_anchor)[1]-row_y(customer_anchor))
    if parsed and not parsed['invoice'].get('payment_method'):
        payment_anchor=next((w for w in words if contains(w['text'],('payment method','payment mthd','payment methd','payment type','طريقة الدفع','نوع الدفع'))),None)
        if payment_anchor:
            add_row(payment_anchor,'payment_method','en')
        else:
            date_anchor=next((w for w in words if re.search(r'(?:\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})',normalize(w['text']))),None)
            if date_anchor:
                candidates.append(dict(kind='payment_method',original=date_anchor,language='en',
                    bbox=[page_width*.48,date_anchor['top'],page_width,min(header_limit,date_anchor['top']+h*12)]))
    if not known_invoice and not any(contains(w['text'],INVOICE_LABELS) for w in words):
        date_anchor=next((w for w in words if re.search(r'(?:\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})',normalize(w['text']))),None)
        if date_anchor:
            candidates.append(dict(kind='invoice_identifier',original=date_anchor,language='en',
                bbox=[page_width*.45,max(0,date_anchor['top']-h*4),page_width,date_anchor['top']+h*.5]))
    if parsed and any(totals.get(key) is None for key in ('subtotal','vat_amount','net_amount')):
        item_bottom=max((e['bbox'][1]+e['bbox'][3] for item in rows for e in item.get('field_evidence',{}).values()
                         if isinstance(e,dict)),default=header or hint or page_height*.5)
        # Totals may sit on either side after a very tall, mostly empty item grid.
        footer_start=min((w['top']-h*.5 for w in words if w['top']>item_bottom and
                          contains(w['text'],('vat summary','tax summary','subtotal','total vat',
                                   'total excluding vat','total including vat','grand total'))),
                         default=max(item_bottom+h*.5,page_height*.5))
        candidates.append(dict(kind='footer_totals',original={},language='en',dpi=400,enhance=True,
            bbox=[0,max(item_bottom,footer_start),page_width,page_height]))
    discount_evidence=parsed.get('field_evidence',{}).get('totals.discount',{}) if parsed else {}
    if parsed and (totals.get('discount') is None or
                   discount_evidence.get('source')!='native_text' and float(discount_evidence.get('confidence') or 0)<80):
        discount_label=next((w for w in words if center(w)[1]>header_limit and
                             contains(w['text'],('discount','الخصم','خصم'))),None)
        if discount_label:
            amount_words=[w for w in words if w is not discount_label and
                          re.search(r'\d',normalize(w['text'])) and
                          0<center(w)[0]-center(discount_label)[0]<h*12 and
                          abs(center(w)[1]-center(discount_label)[1])<h*1.5]
            amount=min(amount_words,key=lambda w:(abs(center(w)[1]-center(discount_label)[1]),
                                                 abs(center(w)[0]-center(discount_label)[0])),default=None)
            if amount:
                x0,y0=amount['left'],amount['top']
                bbox=[x0-h*.5,y0-h*.5,x0+amount['width']+h*.5,y0+amount['height']+h*.5]
            else:
                bbox=[discount_label['left']+discount_label['width']+h*.2,
                      discount_label['top']-h*.8,page_width,
                      discount_label['top']+discount_label['height']+h*.8]
            candidates.append(dict(kind='footer_discount',original=amount or discount_label,
                                   language='en',dpi=400,enhance=True,bbox=bbox))
    for w in sorted(words,key=lambda w:(w['top'],w['left'])):
        text=w['text']
        if center(w)[1]<header_limit:
            if not known_invoice and (contains(text,INVOICE_LABELS) or 'رقم' in text and 'فاتور' in text) and not re.search(r'\b[A-Za-z]+[-/]\d+',text):
                # Exclude the label itself and keep both possible value sides.
                # A short serial crop avoids unrelated handwriting elsewhere in
                # the header and works for either left-to-right or Arabic forms.
                peers=sorted((p for p in words if contains(p['text'],INVOICE_LABELS) and
                              abs(center(p)[1]-center(w)[1])<h),key=lambda p:p['left'])
                if not peers:peers=[w]
                starts=[0]+[p['left']+p['width']+h*.15 for p in peers]
                ends=[p['left']-h*.15 for p in peers]+[page_width]
                for left,right in zip(starts,ends):
                    if right-left<h*3:continue
                    region=dict(kind='invoice_identifier',original=w,language='en',dpi=400,
                                enhance=True,bbox=[left,w['top']-h*.4,right,w['top']+w['height']+h*.4])
                    if not any(r['kind']=='invoice_identifier' and r['bbox']==region['bbox'] for r in candidates):
                        candidates.append(region)
            elif not known_vats and contains(text,('vat','tax code','رقم ضريبة','رقم ضريبه','الرقم الضريبي')) and not re.search(r'(?<!\d)\d{15}(?!\d)',text) and not re.search(r'%|amount|without|including',text,re.I):
                add_row(w,'vat_identifier','en')
                candidates[-1]['bbox'][1] -= h
                candidates[-1].update(dpi=400, enhance=True)
            elif not known_vats and any(t in text for t in ('ضربي','الضري','الضرب')) and not re.search(r'\d{15}',text):
                add_row(w,'vat_identifier','en')
                candidates[-1]['bbox'][1] -= h
                candidates[-1].update(dpi=400, enhance=True)
            elif contains(text,DATE_LABELS) and not contains(text,('supply date','date of supply','تاريخ التوريد')) and not re.search(r'\d{4}',text):
                add_row(w,'date','en')
                candidates[-1].update(dpi=400,enhance=True)
            elif has_arabic(text) and re.search('[A-Za-z]',text) and any(t in text for t in ('شركة','مؤسسة','مؤسسه')) and len(text)>25:
                add(w,'supplier_name','left')
        if header and center(w)[1]>header and numeric(text) is not None and w.get('confidence',100)<80 and re.search(r'\d[.]\d',text):
            add(w,'numeric_cell')
            candidates[-1]['enhance']=True
        elif header and center(w)[1]>header and re.fullmatch(r'\d+[.]\d*[A-Za-z]',text) and numeric(text) is None:
            add(w,'numeric_cell')
    # Header centres can be correct while mixed Arabic/Latin product text is
    # truncated. Re-read the full description cell, not a fraction of the
    # already-detected word box.
    header_words=[w for w in words if header is not None and abs(center(w)[1]-header)<h*3]
    description_heading=min((w for w in header_words if header_match(w['text'],ALIASES['description'])),
                            key=lambda w:abs(center(w)[1]-header),default=None)
    description_bounds=None
    if description_heading:
        dx=center(description_heading)[0]
        neighbors=[w for w in header_words if w is not description_heading and
                   any(header_match(w['text'],ALIASES[key]) for key in ALIASES if key!='description')]
        left=max((w['left']+w['width'] for w in neighbors if center(w)[0]<dx),default=0)
        right=min((w['left'] for w in neighbors if center(w)[0]>dx),default=page_width)
        description_bounds=(left+h*.15,right-h*.15) if right-left>h*4 else None
    for item in rows:
        for missing_key in ('quantity','unit_price','amount'):
            if item.get(missing_key) is not None:
                continue
            heading=next((w for w in words if header_match(w['text'],ALIASES[missing_key]) and abs(center(w)[1]-(header or 0))<h*3),None)
            row_evidence=next((item.get('field_evidence',{}).get(key) for key in ('amount','unit_price','gross_amount','vat_amount','quantity')
                               if isinstance(item.get('field_evidence',{}).get(key),dict)),None)
            if heading and row_evidence:
                _,ry,_,rh=row_evidence['bbox']
                original=dict(left=heading['left'],top=ry,width=heading['width'],height=rh,text='')
                add(original,'numeric_cell')
                # Bound retries by ruled columns or neighbouring column centres;
                # glyph width alone can clip a right-aligned printed price.
                column=heading.get('grid_column')
                if column:
                    left,right=column
                else:
                    cx=center(heading)[0]
                    neighbors=[center(w)[0] for w in header_words if w is not heading and
                               any(header_match(w['text'],ALIASES[k]) for k in ALIASES)]
                    left=max((x for x in neighbors if x<cx),default=cx-heading['width'])
                    right=min((x for x in neighbors if x>cx),default=cx+heading['width'])
                    left,right=(left+cx)/2,(right+cx)/2
                candidates[-1]['bbox']=[left+h*.1,ry-h*.3,right-h*.1,ry+rh+h*.3]
                candidates[-1]['dpi']=400
                candidates[-1].update(language='en', enhance=True)
        if item.get('quantity') is None:
            heading=next((w for w in words if header_match(w['text'],ALIASES['quantity']) and abs(center(w)[1]-(header or 0))<h*3),None)
            row_evidence=item.get('field_evidence',{}).get('amount') or item.get('field_evidence',{}).get('unit_price')
            if heading and row_evidence:
                _,ry,_,rh=row_evidence['bbox']
                unknown=next((w for w in words if abs(center(w)[0]-center(heading)[0])<h and abs(center(w)[1]-(ry+rh/2))<h),None)
                if unknown:
                    add(unknown,'numeric_cell')
                    candidates[-1]['bbox']=[unknown['left']-h*.85,unknown['top']-h*.85,unknown['left']+unknown['width']+h*.85,unknown['top']+unknown['height']+h*.85]
                    candidates[-1]['dpi']=400
        for evidence in item.get('field_evidence',{}).get('description',[]):
            text=evidence['text']
            if evidence.get('source')!='native_text' and float(evidence.get('confidence') or 0)<80:
                w=next((w for w in words if [w.get(k) for k in ('left','top','width','height')]==evidence['bbox']),None)
                if w:
                    if description_bounds:
                        bbox=[description_bounds[0],w['top']-h*.65,description_bounds[1],w['top']+w['height']+h*.65]
                    else:
                        bbox=[w['left']-h*1.5,w['top']-h*.65,w['left']+w['width']+h*1.5,w['top']+w['height']+h*.65]
                    arabic=has_arabic(text)
                    candidates.append(dict(kind='description_ar' if arabic else 'description',
                                           original=w,language='ar' if arabic else 'en',dpi=400,
                                           enhance=True,bbox=bbox))
    # Prioritize identifiers and numeric cells before optional text improvements.
    order={'invoice_identifier':1,'vat_identifier':2,'customer_name_ar':3,'footer_discount':4,
           'description_ar':5,'payment_method':7,'footer_totals':6,'date':6,
           'numeric_cell':0,'supplier_name_ar':8,'supplier_name_en':9,'supplier_name':10,'description':11}
    candidates=sorted(candidates,key=lambda r:(order[r['kind']],r['bbox'][1],r['bbox'][0]))
    # Numeric crops take priority, but a busy table must not consume the whole
    # retry budget before a missing customer or faint item description is read.
    selected=candidates[:10]
    for kind in ('customer_name_ar','description_ar','description'):
        for region in (r for r in candidates if r['kind']==kind and r not in selected):
            if len(selected)>=14:
                break
            selected.append(region)
    candidates=selected
    if incomplete_table and hint is not None:
        footer_y=min((center(w)[1] for w in words if center(w)[1]>hint+2*h and
                      (normalize(w['text']).strip(' :').casefold() in {'total','مجموع'} or contains(w['text'],(
                          'subtotal','grand total','invoice total','total excluding vat','total including vat','vat summary','tax summary',
                          'total vat','before tax','after tax','الإجمالي قبل الضريبة','الإجمالي بعد الضريبة',
                          'إجمالي ضريبة القيمة المضافة','إجمالي المبلغ')))),default=None)
        table_end=footer_y-h*.4 if footer_y is not None else min(page_height*.85,hint+max(12*h,page_height*.25))
        arabic_table=any(has_arabic(w['text']) and hint-3*h<center(w)[1]<table_end for w in words)
        candidates.insert(0,dict(bbox=[0,hint-h,page_width,max(hint+4*h,table_end)],kind='table_cells',original={},
                                 language='ar' if arabic_table else 'en',
                                 recover_text=not rows or any(
                                     reason=='numeric item row lacks a recovered description' or
                                     reason.startswith('printed ') for reason in table_issues),
                                 recover_headers=bool(rows),retry_reasons=table_issues))
    return candidates


def _table_edges(edges,x0,x1):
    """Recover a faint first table rule without dropping the last column."""
    margin=max(4,(x1-x0)*.01)
    edges=sorted(edge for edge in edges if x0+margin<edge<x1-3)
    if len(edges)<3:return edges
    gaps=[b-a for a,b in zip(edges,edges[1:]) if b-a>15]
    if not gaps:return edges
    typical=median(gaps)
    normal=[gap for gap in gaps if gap<=typical*1.8]
    first_width=max(normal or [typical])
    inferred=max(x0+2,edges[0]-first_width)
    if edges[0]-inferred>15:
        edges.insert(0,inferred)
    return edges


def grid_cells(pdf_page,region,dpi,page_width,page_height,rotation_degrees=0,
               rendered_image=None):
    """Use visible vertical rules to re-read merged headers cell by cell."""
    import cv2
    import numpy as np
    x0,y0,x1,y1=region['bbox'];y0=max(0,y0);y1=min(page_height,y1)
    factor=300/dpi
    full=rendered_image or render_upright_page(pdf_page,300,rotation_degrees)
    crop=clamp_crop_bbox(scale_bbox((0,y0,page_width,y1),dpi,300),full.width,full.height)
    gray=cv2.cvtColor(np.array(full.crop(crop)),cv2.COLOR_RGB2GRAY)
    mask=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY_INV,31,10)
    mask=cv2.dilate(mask,cv2.getStructuringElement(cv2.MORPH_RECT,(3,1)))
    vertical=cv2.morphologyEx(mask,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(12,int(gray.shape[0]*.45)))))
    indices=np.where((vertical>0).sum(axis=0)>gray.shape[0]*.4)[0].tolist()
    groups=[]
    for x in indices:
        if not groups or x-groups[-1][-1]>4:groups.append([x])
        else:groups[-1].append(x)
    edges=_table_edges([sum(g)/len(g)/factor for g in groups],x0,x1)
    return [dict(kind='table_cells',bbox=[a+2,y0,b-2,y1],original={},language=region.get('language','ar')) for a,b in zip(edges,edges[1:]) if b-a>15][:16]


def retry_regions(pdf_page,page_payload,model,extract_words,temp_root,missing_numeric_only=False,
                  planned_kinds=None):
    dpi=page_payload.get('render_dpi',200);scale=300/dpi
    page_width=page_payload.get('canonical_width',pdf_page.get_width()*dpi/72)
    page_height=page_payload.get('canonical_height',pdf_page.get_height()*dpi/72)
    rotation_degrees=page_payload.get('rotation_degrees',0)
    rendered={}
    def page_image(render_dpi):
        if render_dpi not in rendered:
            rendered[render_dpi]=render_upright_page(pdf_page,render_dpi,rotation_degrees)
        return rendered[render_dpi]
    word_heights=sorted(float(w.get('height',20)) for w in page_payload.get('words',[]) if w.get('height',0)>0)
    text_height=word_heights[len(word_heights)//2] if word_heights else 20
    retries=[]
    regions=[]
    for region in plan_regions(page_payload):
        if planned_kinds is not None and region['kind'] not in planned_kinds:
            continue
        if missing_numeric_only and (region['kind'] != 'numeric_cell' or region['original'].get('text')):
            continue
        if region['kind']=='table_cells':
            try:
                cells=grid_cells(pdf_page,region,dpi,page_width,page_height,rotation_degrees,
                                 page_image(300))
            except Exception as error:
                # A failed grid detector still leaves the full table crop usable.
                cells=[]
                region=dict(region,planning_error=f'grid detection: {type(error).__name__}: {str(error)[:180]}')
            if len(cells)<4:
                primary=dict(region,kind='table_area',dpi=400)
                if region.get('language')!='ar' or region.get('recover_text'):
                    regions.append(primary)
                if region.get('language')=='ar':
                    secondary=dict(primary,kind='table_area_en',language='en',bbox=list(primary['bbox']))
                    secondary['bbox'][1]+=min(text_height*3,(primary['bbox'][3]-primary['bbox'][1])*.08)
                    secondary['enhance']=True
                    regions.append(secondary)
                continue
            for cell in cells:cell['dpi']=400
            if region.get('language')=='en' and not region.get('recover_headers'):
                h=region['bbox'][3]-region['bbox'][1]
                for cell in cells:
                    cell['bbox'][1]=region['bbox'][1]+min(text_height*3,h*.08)
                    cell['enhance']=True
            # The base Arabic pass already recovered existing descriptions. For
            # partially recovered tables, English numeric/code cells are enough;
            # retain dual-language cell OCR only when no rows were found at all.
            description_label=min((w for w in page_payload.get('words',[]) if header_match(w['text'],ALIASES['description'])),
                                  key=lambda w:abs(center(w)[1]-region['bbox'][1]),default=None)
            description_cell=next((cell for cell in cells if description_label and
                                   cell['bbox'][0]<=center(description_label)[0]<=cell['bbox'][2]),None)
            if region.get('language')!='ar' or region.get('recover_text'):
                regions.extend(cells)
            elif region.get('recover_headers'):
                # Pure-Arabic tables still need a cell-local Arabic header box;
                # keep these crops shallow, while English retries below recover
                # Latin codes/numbers and bilingual header alternatives.
                for cell in cells:
                    header_cell=dict(cell,bbox=list(cell['bbox']))
                    header_cell['bbox'][3]=min(header_cell['bbox'][3],region['bbox'][1]+text_height*5)
                    header_cell['enhance']=True
                    regions.append(header_cell)
            elif description_cell is not None:
                # Keep one Arabic pass for the description column; numeric/code
                # cells use the faster English recognizer.
                description_cell['enhance']=True
                regions.append(description_cell)
            if region.get('language')=='ar':
                for cell in cells:
                    if not region.get('recover_text') and cell is description_cell:
                        continue
                    secondary=dict(cell,kind='table_cells_en',language='en',bbox=list(cell['bbox']))
                    if not region.get('recover_headers'):
                        secondary['bbox'][1]+=min(text_height*3,(secondary['bbox'][3]-secondary['bbox'][1])*.08)
                    secondary['enhance']=True
                    regions.append(secondary)
        else:regions.append(region)
    for i,region in enumerate(regions):
        x0,y0,x1,y1=region['bbox']
        x0=max(0,x0);y0=max(0,y0);x1=min(page_width,x1);y1=min(page_height,y1)
        if x1<=x0 or y1<=y0:continue
        path=Path(temp_root)/f'retry-{i}.png'
        retry_dpi=region.get('dpi',400 if region.get('enhance') else 300)
        scale=retry_dpi/dpi
        try:
            full=page_image(retry_dpi)
            crop=clamp_crop_bbox(scale_bbox((x0,y0,x1,y1),dpi,retry_dpi),full.width,full.height)
            picture=full.crop(crop)
            if region.get('enhance'):
                import cv2
                import numpy as np
                gray=cv2.cvtColor(np.array(picture),cv2.COLOR_RGB2GRAY)
                gray=cv2.normalize(gray,None,0,255,cv2.NORM_MINMAX)
                gray=cv2.erode(gray,np.ones((3,3),np.uint8))
                if not cv2.imwrite(str(path),gray):
                    raise OSError(f'Cannot save OCR crop {path}')
            else:picture.save(path)
        except Exception as error:
            retries.append(dict(kind=region['kind'],bbox=[x0,y0,x1,y1],
                                original=region['original'],words=[],
                                error=f'crop preparation: {type(error).__name__}: {str(error)[:180]}'))
            continue
        found=[]
        errors=[region['planning_error']] if region.get('planning_error') else []
        def add_result(result):
            for word in extract_words(result):
                word.update(left=word['left']/scale+x0,top=word['top']/scale+y0,
                            width=word['width']/scale,height=word['height']/scale,
                            polygon=[[x/scale+x0,y/scale+y0] for x,y in word['polygon']],
                            source='targeted_ocr',retry_kind=region['kind'])
                if region['kind'] in {'table_cells','table_cells_en'}:
                    # OCR glyph alignment can differ from the ruled cell centre.
                    word.update(grid_column=[x0,x1],grid_center_x=(x0+x1)/2)
                found.append(word)
        options={'text_det_thresh':.1,'text_det_box_thresh':.2} if region['kind'] in {
            'numeric_cell','table_cells','table_cells_en','table_area','table_area_en',
            'footer_totals','footer_discount','customer_name_ar','description_ar','description',
            'date','invoice_identifier'} else {}
        predictor=None
        try:
            predictor=model(region['language']) if region.get('language') else model()
            for result in predictor.predict(str(path),**options):
                add_result(result)
        except Exception as error:
            # A failed crop must not erase the successful retries on this page.
            errors.append(f'enhanced OCR: {type(error).__name__}: {str(error)[:180]}')
        faint_arabic=region['kind'] in {'customer_name_ar','description_ar'}
        def strong_arabic():
            return any(float(w.get('confidence') or 0)>=80 and
                       len(re.findall(r'[\u0600-\u06ff]',w['text']))>=
                       (8 if region['kind']=='customer_name_ar' else 6) and
                       (len(w['text'].split())>=2 and not contains(w['text'],CUSTOMER_SECTION_LABELS)
                        if region['kind']=='customer_name_ar' else
                        not contains(w['text'],ALIASES['description']))
                       for w in found)
        needs_plain=(
            region['kind']=='customer_name_ar' or faint_arabic and not strong_arabic() or
            region['kind']=='numeric_cell' and not any(
                float(w.get('confidence') or 0)>=85 and numeric(w['text']) is not None for w in found) or
            region['kind']=='invoice_identifier' and not any(
                float(w.get('confidence') or 0)>=75 and
                re.fullmatch(r'\d{3,14}',w['text'].strip(' .:#')) for w in found) or
            region['kind']=='footer_totals' and
            sum(numeric(w['text']) is not None for w in found)<3 or
            region['kind']=='footer_discount' and
            not any(numeric(w['text']) is not None for w in found)
        )
        plain_path=None
        if needs_plain and predictor is not None:
            # Dilation can join faint strokes or damage light coloured print.
            # Retry the original crop so recognition sees both image versions.
            plain_path=Path(temp_root)/f'retry-{i}-plain.png'
            try:
                picture.save(plain_path)
                for result in predictor.predict(str(plain_path),**options):
                    add_result(result)
            except Exception as error:
                errors.append(f'plain OCR: {type(error).__name__}: {str(error)[:180]}')
        if predictor is not None and region['kind']=='supplier_name_ar' and not any(contains(w['text'],('مؤسسة','مؤسسه','شركة')) and len(w['text'])>12 for w in found):
            # The Arabic name may be fragmented by detection; read the complete label line.
            import cv2
            try:
                for result in predictor.paddlex_pipeline.text_rec_model([cv2.imread(str(path))]):
                    found.append(dict(text=result['rec_text'],confidence=float(result['rec_score'])*100,
                                      left=x0,top=y0,width=x1-x0,height=y1-y0,
                                      polygon=[[x0,y0],[x1,y0],[x1,y1],[x0,y1]],
                                      source='targeted_ocr',retry_kind=region['kind']))
            except Exception as error:
                errors.append(f'direct OCR: {type(error).__name__}: {str(error)[:180]}')
        direct_needed=(region['kind']=='customer_name_ar' or
                       faint_arabic and not strong_arabic() or
                       region['kind']=='invoice_identifier' and not any(
                           float(w.get('confidence') or 0)>=75 and
                           re.fullmatch(r'\d{3,14}',w['text'].strip(' .:#')) for w in found) or
                       region['kind']=='numeric_cell' and not any(
                           float(w.get('confidence') or 0)>=85 and numeric(w['text']) is not None for w in found))
        if direct_needed and predictor is not None:
            # The detector can miss an entire faint dot-matrix line. A direct
            # recognition pass over the original value crop may recover it.
            import cv2
            try:
                source_path=plain_path if plain_path and plain_path.is_file() else path
                for result in predictor.paddlex_pipeline.text_rec_model([cv2.imread(str(source_path))]):
                    found.append(dict(text=result['rec_text'],confidence=float(result['rec_score'])*100,
                                      left=x0,top=y0,width=x1-x0,height=y1-y0,
                                      polygon=[[x0,y0],[x1,y0],[x1,y1],[x0,y1]],
                                      source='targeted_ocr',retry_kind=region['kind']))
            except Exception as error:
                errors.append(f'direct OCR: {type(error).__name__}: {str(error)[:180]}')
        retry=dict(kind=region['kind'],bbox=[x0,y0,x1,y1],original=region['original'],words=found)
        if errors:
            retry['error']='; '.join(errors)
        retries.append(retry)
    return retries


def merge_retries(page, retries):
    """Retain raw alternatives; only promote typed, confident region candidates."""
    words=list(page['words']);accepted=[]
    rejected=list(page.get('targeted_ocr',{}).get('rejected',[]))
    heights=sorted(float(w.get('height',20)) for w in words if w.get('height',0)>0)
    text_height=heights[len(heights)//2] if heights else 20
    for retry in retries:
        previous_words=list(words)
        accepted_start=len(accepted)
        kind=retry['kind'];candidates=[]
        for word in retry['words']:
            text=word['text'].strip()
            if kind in {'footer_totals','footer_discount'}:
                confidence_floor=70
            elif kind=='invoice_identifier':
                confidence_floor=75
            elif kind in {'supplier_name','supplier_name_ar','supplier_name_en','customer_name_ar','payment_method','description','description_ar'}:
                confidence_floor=80
            else:
                confidence_floor=85
            if word.get('confidence',0)<confidence_floor:continue
            if kind=='vat_identifier' and re.search(r'(?<!\d)\d{15}(?!\d)',text):
                candidates.append(word)
            elif kind=='invoice_identifier':
                ids=re.findall(r'\b[A-Za-z]{2,}[-/][A-Za-z0-9/-]*\d[A-Za-z0-9/-]*\b',text)
                if len(ids)==1:
                    candidates.append(dict(word,text=ids[0],raw_text=text))
                elif re.fullmatch(r'\d{3,}',text.strip(' .:#')) and len(text.strip(' .:#')) != 15:
                    candidates.append(dict(word,text=text.strip(' .:#'),raw_text=text))
            elif kind=='numeric_cell' and numeric(text) is not None:
                original=retry['original']
                if abs(center(word)[1]-center(original)[1])<=max(original.get('height',20),word.get('height',20))*.75:
                    candidates.append(word)
            elif kind=='footer_discount' and numeric(text) is not None:
                candidates.append(word)
            elif kind in {'table_cells','table_area'}:
                candidates.append(word)
            elif kind=='footer_totals' and (numeric(text) is not None or re.search(r'[A-Za-z]{3,}',text)):
                candidates.append(word)
            elif kind in {'table_cells_en','table_area_en'} and (numeric(text) is not None or
                    re.fullmatch(r'(?i)pcs?\.?|sets?|kg|m|ltr|box|roll',text) or
                    re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/.* -]{2,}',text)):
                candidates.append(word)
            elif kind=='date' and re.search(r'(?:\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})',text):
                date=re.search(r'(?:\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})',text)[0]
                time=re.search(r'\d{2}:\d{2}:\d{2}',text)
                candidates.append(dict(word,text=date,raw_text=text,raw_time=time[0] if time else None))
            elif kind=='description' and not contains(text,ALIASES['description']) and (
                    re.search(r'[A-Za-z]{3,}',text) or re.search(r'(?i)\b[A-Z]+\d+|\b\d+[/-]\d+\b',text)):
                candidates.append(word)
            elif kind=='description_ar' and len(re.findall(r'[\u0600-\u06ff]',text))>=6 and not contains(text,ALIASES['description']):
                candidates.append(word)
            elif kind=='supplier_name' and re.search(r'[A-Za-z]{3,}',text) and len(text)>8:
                candidates.append(word)
            elif kind=='supplier_name_ar' and has_arabic(text) and contains(text,('مؤسسة','مؤسسه','شركة')) and len(text)>12:
                candidates.append(word)
            elif kind=='supplier_name_en' and re.search(r'[A-Za-z]{3,}',text) and contains(text,('company','trading','est','establishment','co')):
                candidates.append(word)
            elif (kind=='customer_name_ar' and len(re.findall(r'[\u0600-\u06ff]',text))>=8 and
                  len(text.split())>=2 and not contains(text,CUSTOMER_SECTION_LABELS) and
                  not address_label(text)):
                candidates.append(word)
            elif kind=='payment_method' and contains(text,('cash','card','credit','mada','span','network')):
                candidates.append(word)
        if kind in {'numeric_cell','vat_identifier','invoice_identifier','date'} and len(candidates)>1:
            candidates=[min(candidates,key=lambda word:(float(word.get('height',text_height))>text_height*1.8,
                abs(center(word)[0]-center(retry['original'])[0])+abs(center(word)[1]-center(retry['original'])[1])*3))]
        if kind in {'numeric_cell','vat_identifier','invoice_identifier','date'} and len(candidates)!=1:
            continue
        if kind=='numeric_cell' and candidates:
            words=[w for w in words if w!=retry['original']]
        if kind in {'description_ar','description'} and candidates:
            original=retry['original']
            alphabet=r'[\u0600-\u06ff]' if kind=='description_ar' else r'[A-Za-z]'
            old_letters=len(re.findall(alphabet,original.get('text','')))
            new_letters=sum(len(re.findall(alphabet,w['text'])) for w in candidates)
            if original in words and float(original.get('confidence') or 0)<80 and new_letters>=max(3,old_letters*.7):
                words.remove(original)
        for w in candidates:
            # Do not duplicate a successfully read same-language source phrase.
            if any(w['text'].casefold()==old['text'].casefold() and abs(center(w)[1]-center(old)[1])<old.get('height',20) and abs(center(w)[0]-center(old)[0])<max(w.get('width',1),old.get('width',1))*.5 for old in words):continue
            words.append(w);accepted.append(dict(kind=kind,text=w['text'],bbox=[w[k] for k in ('left','top','width','height')]))
        if kind=='numeric_cell' and candidates:
            before,_,_=table(previous_words)
            after,_,_=table(words)
            def row_y(item):
                evidence=item.get('field_evidence',{})
                boxes=[ev['bbox'] for key,ev in evidence.items()
                       if key!='description' and isinstance(ev,dict) and 'bbox' in ev]
                return median(b[1]+b[3]/2 for b in boxes) if boxes else None
            remaining=[row_y(item) for item in after]
            lost=any(row_y(item) is not None and not any(
                value is not None and abs(row_y(item)-value)<text_height
                for value in remaining) for item in before)
            if lost:
                words=previous_words
                del accepted[accepted_start:]
                rejected.append(dict(kind=kind,bbox=retry.get('bbox'),
                                     reason='numeric retry would remove an existing item row'))
    page['words']=words
    page['text']='\n'.join(w['text'] for w in words)
    page['targeted_ocr']={'attempted_regions':len(retries),'accepted':accepted,'alternatives':retries,
                          'rejected':rejected}
    errors=[f"{retry['kind']}: {retry['error']}" for retry in retries if retry.get('error')]
    if errors:
        previous=page.get('targeted_ocr_error')
        page['targeted_ocr_error']='; '.join(dict.fromkeys(([previous] if previous else [])+errors))[:1000]
    return page
