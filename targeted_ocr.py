"""Bounded region re-OCR with page-coordinate provenance and retained alternatives."""
import re
from contextlib import closing
from pathlib import Path
from invoice_formatter import contains, center, has_arabic, normalize
from document_regions import invoice_words
from layout_invoice import (
    ALIASES, CUSTOMER_SECTION_LABELS, DATE_LABELS, INVOICE_LABELS,
    header_hint, header_match, numeric, parse_layout, table,
)


def plan_regions(page):
    words,_=invoice_words(page)
    h=sorted([w.get('height',20) for w in words])[len(words)//2] if words else 20
    rows,header,_=table(words)
    hint=header_hint(words)
    if header is None:
        header=hint
    page_width=page.get('width',1)*page.get('render_dpi',200)/72
    page_height=page.get('height',1)*page.get('render_dpi',200)/72
    header_limit=header if header is not None else page_height*.5
    candidates=[]
    parsed=parse_layout([page],'','')
    totals=parsed.get('totals',{}) if parsed else {}
    # A totals/arithmetic review is not evidence that table detection failed. In
    # particular, some printed invoices are internally inconsistent. Re-reading
    # every ruled cell in two languages cannot repair that source discrepancy and
    # is the most expensive part of the pipeline. Reserve the full-table retry for
    # missing row structure; individual prices/amounts and totals get tight crops.
    code_header=any(header_match(w['text'],ALIASES['item_code']) and
                    (header is None or abs(center(w)[1]-header)<h*3) for w in words)
    quantity_header=any(header_match(w['text'],ALIASES['quantity']) and
                        (header is None or abs(center(w)[1]-header)<h*3) for w in words)
    incomplete_table=(not rows or
                      (code_header and any(item.get('item_code') is None for item in rows)) or
                      (quantity_header and any(item.get('quantity') is None for item in rows)))
    known_invoice=parsed and parsed['invoice'].get('invoice_number')
    known_invoice_evidence=parsed.get('field_evidence',{}).get('invoice.invoice_number') if parsed else None
    if known_invoice_evidence and known_invoice_evidence.get('bbox'):
        # Oversized text inside a form field is often a handwritten note. Re-read
        # the row so a smaller printed serial beside either bilingual label can win.
        known_invoice=known_invoice and known_invoice_evidence['bbox'][3]<=h*1.8
    if parsed and not parsed['supplier'].get('name_ar'):
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
    if parsed and not parsed['supplier'].get('name_en'):
        name_anchor=next((w for w in words if contains(w['text'],('مؤسسة','مؤسسه','شركة')) and center(w)[1]<header_limit*.5),None)
        if name_anchor:
            cluster=[w for w in words if abs(center(w)[1]-center(name_anchor)[1])<h]
            candidates.append(dict(kind='supplier_name_en',original=name_anchor,language='en',
                bbox=[max(0,min(w['left'] for w in cluster)-h),max(0,min(w['top'] for w in cluster)-h*.4),
                      min(page_width,max(w['left']+w['width'] for w in cluster)+h),
                      min(page_height,max(w['top']+w['height'] for w in cluster)+h*4)]))
    customer_value=parsed.get('customer',{}).get('name') if parsed else None
    bad_customer=(not customer_value or contains(customer_value,CUSTOMER_SECTION_LABELS) or
                  re.search(r'(?i)(?:building|post\s*code|add\s*no|المبنى|الرمز\s*البريدي)\s*\d',customer_value))
    if bad_customer:
        customer_anchor=next((w for w in words if contains(w['text'],CUSTOMER_SECTION_LABELS) and
                              not contains(w['text'],('customer code','cus code','customer no','vat','tax','كود العميل','رقم العميل','الضريبي'))),None)
        if customer_anchor:add_row(customer_anchor,'customer_name_ar','ar')
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
    if parsed and (totals.get('subtotal') is None or totals.get('net_amount') is None):
        item_bottom=max((e['bbox'][1]+e['bbox'][3] for item in rows for e in item.get('field_evidence',{}).values()
                         if isinstance(e,dict)),default=header or hint or page_height*.5)
        # Totals may sit on either side after a very tall, mostly empty item grid.
        candidates.append(dict(kind='footer_totals',original={},language='en',dpi=400,enhance=True,
            bbox=[0,max(item_bottom+h*.5,page_height*.5),page_width,page_height]))
    for w in sorted(words,key=lambda w:(w['top'],w['left'])):
        text=w['text']
        if center(w)[1]<header_limit:
            if not known_invoice and (contains(text,INVOICE_LABELS) or 'رقم' in text and 'فاتور' in text) and not re.search(r'\b[A-Za-z]+[-/]\d+',text):
                # Bilingual forms commonly place the value in a separate box to the label's left.
                add_row(w,'invoice_identifier','en')
                candidates[-1].update(dpi=400,enhance=True)
            elif contains(text,('vat','tax code','رقم ضريبة','رقم ضريبه','الرقم الضريبي')) and not re.search(r'(?<!\d)\d{15}(?!\d)',text) and not re.search(r'%|amount|without|including',text,re.I):
                add_row(w,'vat_identifier','en')
            elif any(t in text for t in ('ضربي','الضري','الضرب')) and not re.search(r'\d{15}',text):
                add_row(w,'vat_identifier','en')
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
                candidates[-1]['bbox']=[heading['left']-h,ry-h*.7,heading['left']+heading['width']+h,ry+rh+h*.7]
                candidates[-1]['dpi']=400
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
            if has_arabic(text) and not re.search('[A-Za-z]',text) and len(text)>=8 and not re.search('[A-Za-z]',item.get('description') or ''):
                w=next((w for w in words if [w.get(k) for k in ('left','top','width','height')]==evidence['bbox']),None)
                if w:add(w,'description','left')
    if incomplete_table and hint is not None:
        description_label=next((w for w in words if header_match(w['text'],ALIASES['description']) and abs(center(w)[1]-hint)<h*2),None)
        if description_label:
            for w in words:
                if hint+h*.5<center(w)[1]<hint+h*3 and abs(center(w)[0]-center(description_label)[0])<h*4 and has_arabic(w['text']) and not re.search('[A-Za-z]',w['text']) and len(w['text'])>=8:
                    add(w,'description','left')
    # Prioritize identifiers and numeric cells before optional text improvements.
    order={'invoice_identifier':0,'vat_identifier':1,'customer_name_ar':2,'payment_method':3,'footer_totals':4,'date':5,
           'numeric_cell':6,'supplier_name_ar':7,'supplier_name_en':8,'supplier_name':9,'description':10}
    candidates=sorted(candidates,key=lambda r:(order[r['kind']],r['bbox'][1],r['bbox'][0]))[:10]
    if incomplete_table and hint is not None:
        footer_y=min((center(w)[1] for w in words if center(w)[1]>hint+2*h and
                      (normalize(w['text']).strip(' :').casefold() in {'total','مجموع'} or contains(w['text'],(
                          'subtotal','grand total','invoice total','total excluding vat','total including vat',
                          'total vat','before tax','after tax','الإجمالي قبل الضريبة','الإجمالي بعد الضريبة',
                          'إجمالي ضريبة القيمة المضافة','إجمالي المبلغ')))),default=None)
        table_end=footer_y-h*.4 if footer_y is not None else min(page_height*.85,hint+max(12*h,page_height*.25))
        arabic_table=any(has_arabic(w['text']) and hint-3*h<center(w)[1]<table_end for w in words)
        candidates.insert(0,dict(bbox=[0,hint-h,page_width,max(hint+4*h,table_end)],kind='table_cells',original={},
                                 language='ar' if arabic_table else 'en',recover_text=not rows))
    return candidates


def grid_cells(pdf_page,region,dpi,page_width,page_height):
    """Use visible vertical rules to re-read merged headers cell by cell."""
    import cv2
    import numpy as np
    x0,y0,x1,y1=region['bbox'];y0=max(0,y0);y1=min(page_height,y1)
    factor=300/dpi
    crop=(0,(page_height-y1)*72/dpi,0,y0*72/dpi)
    with closing(pdf_page.render(scale=300/72,crop=crop)) as bitmap:
        gray=cv2.cvtColor(np.array(bitmap.to_pil().convert('RGB')),cv2.COLOR_RGB2GRAY)
    mask=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY_INV,31,10)
    mask=cv2.dilate(mask,cv2.getStructuringElement(cv2.MORPH_RECT,(3,1)))
    vertical=cv2.morphologyEx(mask,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(12,int(gray.shape[0]*.45)))))
    indices=np.where((vertical>0).sum(axis=0)>gray.shape[0]*.4)[0].tolist()
    groups=[]
    for x in indices:
        if not groups or x-groups[-1][-1]>4:groups.append([x])
        else:groups[-1].append(x)
    edges=[sum(g)/len(g)/factor for g in groups]
    return [dict(kind='table_cells',bbox=[a+2,y0,b-2,y1],original={},language=region.get('language','ar')) for a,b in zip(edges,edges[1:]) if b-a>15][:12]


def retry_regions(pdf_page,page_payload,model,extract_words,temp_root):
    dpi=page_payload.get('render_dpi',200);scale=300/dpi
    page_width=pdf_page.get_width()*dpi/72;page_height=pdf_page.get_height()*dpi/72
    word_heights=sorted(float(w.get('height',20)) for w in page_payload.get('words',[]) if w.get('height',0)>0)
    text_height=word_heights[len(word_heights)//2] if word_heights else 20
    retries=[]
    regions=[]
    for region in plan_regions(page_payload):
        if region['kind']=='table_cells':
            cells=grid_cells(pdf_page,region,dpi,page_width,page_height)
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
            if region.get('language')=='en':
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
                    secondary['bbox'][1]+=min(text_height*3,(secondary['bbox'][3]-secondary['bbox'][1])*.08)
                    secondary['enhance']=True
                    regions.append(secondary)
        else:regions.append(region)
    for i,region in enumerate(regions):
        x0,y0,x1,y1=region['bbox']
        x0=max(0,x0);y0=max(0,y0);x1=min(page_width,x1);y1=min(page_height,y1)
        if x1<=x0 or y1<=y0:continue
        path=Path(temp_root)/f'retry-{i}.png'
        crop=(x0*72/dpi,(page_height-y1)*72/dpi,(page_width-x1)*72/dpi,y0*72/dpi)
        retry_dpi=region.get('dpi',400 if region.get('enhance') else 300)
        scale=retry_dpi/dpi
        with closing(pdf_page.render(scale=retry_dpi/72,crop=crop)) as bitmap:
            picture=bitmap.to_pil().convert('RGB')
            if region.get('enhance'):
                import cv2
                import numpy as np
                gray=cv2.cvtColor(np.array(picture),cv2.COLOR_RGB2GRAY)
                gray=cv2.normalize(gray,None,0,255,cv2.NORM_MINMAX)
                gray=cv2.erode(gray,np.ones((3,3),np.uint8))
                cv2.imwrite(str(path),gray)
            else:picture.save(path)
        found=[]
        predictor=model(region.get('language','ar')) if region['kind'] in {'table_cells','table_cells_en','table_area','table_area_en','footer_totals','supplier_name_ar','supplier_name_en','customer_name_ar','payment_method'} else model()
        options={'text_det_thresh':.1,'text_det_box_thresh':.2} if region['kind'] in {'numeric_cell','table_cells','table_cells_en','table_area','table_area_en','footer_totals','date','invoice_identifier'} else {}
        for result in predictor.predict(str(path),**options):
            for word in extract_words(result):
                word.update(left=word['left']/scale+x0,top=word['top']/scale+y0,
                            width=word['width']/scale,height=word['height']/scale,
                            polygon=[[x/scale+x0,y/scale+y0] for x,y in word['polygon']],
                            source='targeted_ocr',retry_kind=region['kind'])
                found.append(word)
        if region['kind']=='supplier_name_ar' and not any(contains(w['text'],('مؤسسة','مؤسسه','شركة')) and len(w['text'])>12 for w in found):
            # The Arabic name may be fragmented by detection; read the complete label line.
            import cv2
            for result in predictor.paddlex_pipeline.text_rec_model([cv2.imread(str(path))]):
                found.append(dict(text=result['rec_text'],confidence=float(result['rec_score'])*100,
                                  left=x0,top=y0,width=x1-x0,height=y1-y0,
                                  polygon=[[x0,y0],[x1,y0],[x1,y1],[x0,y1]],
                                  source='targeted_ocr',retry_kind=region['kind']))
        retries.append(dict(kind=region['kind'],bbox=[x0,y0,x1,y1],original=region['original'],words=found))
    return retries


def merge_retries(page, retries):
    """Retain raw alternatives; only promote typed, confident region candidates."""
    words=list(page['words']);accepted=[]
    heights=sorted(float(w.get('height',20)) for w in words if w.get('height',0)>0)
    text_height=heights[len(heights)//2] if heights else 20
    for retry in retries:
        kind=retry['kind'];candidates=[]
        for word in retry['words']:
            text=word['text'].strip()
            confidence_floor=80 if kind in {'supplier_name','supplier_name_ar','supplier_name_en','customer_name_ar','payment_method','description'} else 85
            if word.get('confidence',0)<confidence_floor:continue
            if kind=='vat_identifier' and re.search(r'(?<!\d)\d{15}(?!\d)',text):
                candidates.append(word)
            elif kind=='invoice_identifier':
                ids=re.findall(r'\b[A-Za-z]{2,}[-/][A-Za-z0-9/-]*\d[A-Za-z0-9/-]*\b',text)
                if len(ids)==1:
                    candidates.append(dict(word,text=ids[0],raw_text=text))
                elif re.fullmatch(r'\d{3,}',text):
                    candidates.append(word)
            elif kind=='numeric_cell' and numeric(text) is not None:
                original=retry['original']
                if abs(center(word)[1]-center(original)[1])<=max(original.get('height',20),word.get('height',20))*.75:
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
            elif kind=='description' and re.search(r'[A-Za-z]{3,}',text):
                candidates.append(word)
            elif kind=='supplier_name' and re.search(r'[A-Za-z]{3,}',text) and len(text)>8:
                candidates.append(word)
            elif kind=='supplier_name_ar' and has_arabic(text) and contains(text,('مؤسسة','مؤسسه','شركة')) and len(text)>12:
                candidates.append(word)
            elif kind=='supplier_name_en' and re.search(r'[A-Za-z]{3,}',text) and contains(text,('company','trading','est','establishment','co')):
                candidates.append(word)
            elif kind=='customer_name_ar' and has_arabic(text) and contains(text,('مؤسسة','مؤسسه','شركة','مقاولات')) and len(text)>12:
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
        for w in candidates:
            # Do not duplicate a successfully read same-language source phrase.
            if any(w['text'].casefold()==old['text'].casefold() and abs(center(w)[1]-center(old)[1])<old.get('height',20) and abs(center(w)[0]-center(old)[0])<max(w.get('width',1),old.get('width',1))*.5 for old in words):continue
            words.append(w);accepted.append(dict(kind=kind,text=w['text'],bbox=[w[k] for k in ('left','top','width','height')]))
    page['words']=words
    page['text']='\n'.join(w['text'] for w in words)
    page['targeted_ocr']={'attempted_regions':len(retries),'accepted':accepted,'alternatives':retries}
    return page
