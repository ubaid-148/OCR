"""Bounded region re-OCR with page-coordinate provenance and retained alternatives."""
import re
from contextlib import closing
from pathlib import Path
from invoice_formatter import contains, center, has_arabic
from document_regions import invoice_words
from layout_invoice import table, numeric, header_hint, ALIASES, parse_layout


def plan_regions(page):
    words,_=invoice_words(page)
    h=sorted([w.get('height',20) for w in words])[len(words)//2] if words else 20
    rows,header,_=table(words)
    incomplete_table=not rows or any(item.get('quantity') is None for item in rows)
    hint=header_hint(words)
    if header is None:
        header=hint
    page_width=page.get('width',1)*page.get('render_dpi',200)/72
    page_height=page.get('height',1)*page.get('render_dpi',200)/72
    header_limit=header if header is not None else page_height*.5
    candidates=[]
    parsed=parse_layout([page],'','')
    known_invoice=parsed and parsed['invoice'].get('invoice_number')
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
    for w in sorted(words,key=lambda w:(w['top'],w['left'])):
        text=w['text']
        if center(w)[1]<header_limit:
            if not known_invoice and (contains(text,('invoice no','invoice number','inv no','رقم الفاتورة')) or 'رقم' in text and 'فاتور' in text) and not re.search(r'\b[A-Za-z]+[-/]\d+',text):
                add(w,'invoice_identifier','identifier')
                # Arabic forms can place the value to the left of its label.
                if not re.search('[A-Za-z]',text):add(w,'invoice_identifier','left')
            elif contains(text,('vat','رقم ضريبة','رقم ضريبه','الرقم الضريبي')) and not re.search(r'(?<!\d)\d{15}(?!\d)',text) and not re.search(r'%|amount|without|including',text,re.I):
                add(w,'vat_identifier','right')
            elif any(t in text for t in ('ضربي','الضري','الضرب')) and not re.search(r'\d{15}',text):
                add(w,'vat_identifier','whole')
            elif 'تاريخ' in text and not re.search(r'\d{4}',text):
                add(w,'date','whole')
            elif has_arabic(text) and re.search('[A-Za-z]',text) and any(t in text for t in ('شركة','مؤسسة','مؤسسه')) and len(text)>25:
                add(w,'supplier_name','left')
        if header and center(w)[1]>header and numeric(text) is not None and w.get('confidence',100)<80 and re.search(r'\d[.]\d',text):
            add(w,'numeric_cell')
            candidates[-1]['enhance']=True
        elif header and center(w)[1]>header and re.fullmatch(r'\d+[.]\d*[A-Za-z]',text) and numeric(text) is None:
            add(w,'numeric_cell')
    for item in rows:
        if item.get('quantity') is None:
            heading=next((w for w in words if contains(w['text'],ALIASES['quantity']) and abs(center(w)[1]-(header or 0))<h*3),None)
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
        description_label=next((w for w in words if contains(w['text'],ALIASES['description']) and abs(center(w)[1]-hint)<h*2),None)
        if description_label:
            for w in words:
                if hint+h*.5<center(w)[1]<hint+h*3 and abs(center(w)[0]-center(description_label)[0])<h*4 and has_arabic(w['text']) and not re.search('[A-Za-z]',w['text']) and len(w['text'])>=8:
                    add(w,'description','left')
    # Prioritize identifiers and numeric cells before optional text improvements.
    order={'numeric_cell':0,'invoice_identifier':1,'vat_identifier':2,'supplier_name_ar':3,'date':4,'supplier_name':5,'description':6}
    candidates=sorted(candidates,key=lambda r:(order[r['kind']],r['bbox'][1],r['bbox'][0]))[:6]
    if incomplete_table and hint is not None:
        candidates.insert(0,dict(bbox=[0,hint-h,page_width,hint+3*h],kind='table_cells',original={},language='en' if any(contains(w['text'],('unit price',)) for w in words) else 'ar'))
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
    return [dict(kind='table_cells',bbox=[a+2,y0,b-2,y1],original={},language=region.get('language','ar')) for a,b in zip(edges,edges[1:]) if b-a>15][:8]


def retry_regions(pdf_page,page_payload,model,extract_words,temp_root):
    dpi=page_payload.get('render_dpi',200);scale=300/dpi
    page_width=pdf_page.get_width()*dpi/72;page_height=pdf_page.get_height()*dpi/72
    retries=[]
    regions=[]
    for region in plan_regions(page_payload):
        if region['kind']=='table_cells':
            cells=grid_cells(pdf_page,region,dpi,page_width,page_height)
            if region.get('language')=='en':
                h=region['bbox'][3]-region['bbox'][1]
                for cell in cells:
                    cell['bbox'][1]=region['bbox'][1]+h*.45
                    cell['enhance']=True
            regions.extend(cells)
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
        predictor=model(region.get('language','ar')) if region['kind'] in {'table_cells','supplier_name_ar'} else model()
        options={'text_det_thresh':.1,'text_det_box_thresh':.2} if region['kind']=='numeric_cell' else {}
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
    for retry in retries:
        kind=retry['kind'];candidates=[]
        for word in retry['words']:
            text=word['text'].strip()
            if word.get('confidence',0)<85:continue
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
            elif kind=='table_cells':
                candidates.append(word)
            elif kind=='date' and re.search(r'\d{1,2}[-/]\d{1,2}[-/]\d{4}',text):
                date=re.search(r'\d{1,2}[-/]\d{1,2}[-/]\d{4}',text)[0]
                time=re.search(r'\d{2}:\d{2}:\d{2}',text)
                candidates.append(dict(word,text=date,raw_text=text,raw_time=time[0] if time else None))
            elif kind=='description' and re.search(r'[A-Za-z]{3,}',text):
                candidates.append(word)
            elif kind=='supplier_name' and re.search(r'[A-Za-z]{3,}',text) and len(text)>8:
                candidates.append(word)
            elif kind=='supplier_name_ar' and has_arabic(text) and contains(text,('مؤسسة','مؤسسه','شركة')) and len(text)>12:
                candidates.append(word)
        if kind in {'numeric_cell','vat_identifier','invoice_identifier'} and len(candidates)!=1:
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
