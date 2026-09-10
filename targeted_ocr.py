"""Bounded region re-OCR with page-coordinate provenance and retained alternatives."""
import re
from contextlib import closing
from pathlib import Path
from invoice_formatter import contains, center, has_arabic
from document_regions import invoice_words
from layout_invoice import table, numeric


def plan_regions(page):
    words,_=invoice_words(page)
    h=sorted([w.get('height',20) for w in words])[len(words)//2] if words else 20
    rows,header,_=table(words)
    candidates=[]
    def add(w,kind,part='whole'):
        x0,y0=w['left'],w['top'];x1=x0+w['width'];y1=y0+w['height']
        if part=='left':x1=x0+w['width']*.62
        if part=='right':x0=x0+w['width']*.38
        if part=='identifier':x0=x0+w['width']*.60
        candidates.append(dict(bbox=[x0-h*.2,y0-h*.25,x1+h*.2,y1+h*.25],kind=kind,original=w))
    for w in sorted(words,key=lambda w:(w['top'],w['left'])):
        text=w['text']
        if header and center(w)[1]<header:
            if (contains(text,('invoice no','invoice number','inv no','رقم الفاتورة')) or 'رقم' in text and 'فاتور' in text) and not re.search(r'\b[A-Za-z]+[-/]\d+',text):
                add(w,'invoice_identifier','identifier')
            elif contains(text,('vat','رقم ضريبة','رقم ضريبه','الرقم الضريبي')) and not re.search(r'(?<!\d)\d{15}(?!\d)',text) and not re.search(r'%|amount|without|including',text,re.I):
                add(w,'vat_identifier','right')
            elif has_arabic(text) and any(t in text for t in ('شركة','مؤسسة','مؤسسه')) and len(text)>25:
                add(w,'supplier_name','left')
        if header and center(w)[1]>header and re.fullmatch(r'\d+[.]\d*[A-Za-z]',text) and numeric(text) is None:
            add(w,'numeric_cell')
    for item in rows:
        for evidence in item.get('field_evidence',{}).get('description',[]):
            text=evidence['text']
            if has_arabic(text) and not re.search('[A-Za-z]',text) and len(text)>=8 and not re.search('[A-Za-z]',item.get('description') or ''):
                w=next((w for w in words if [w.get(k) for k in ('left','top','width','height')]==evidence['bbox']),None)
                if w:add(w,'description','left')
    # Prioritize identifiers and numeric cells before optional text improvements.
    order={'invoice_identifier':0,'vat_identifier':1,'numeric_cell':2,'supplier_name':3,'description':4}
    return sorted(candidates,key=lambda r:(order[r['kind']],r['bbox'][1],r['bbox'][0]))[:6]


def retry_regions(pdf_page,page_payload,model,extract_words,temp_root):
    dpi=page_payload.get('render_dpi',200);scale=300/dpi
    page_width=pdf_page.get_width()*dpi/72;page_height=pdf_page.get_height()*dpi/72
    retries=[]
    for i,region in enumerate(plan_regions(page_payload)):
        x0,y0,x1,y1=region['bbox']
        x0=max(0,x0);y0=max(0,y0);x1=min(page_width,x1);y1=min(page_height,y1)
        if x1<=x0 or y1<=y0:continue
        path=Path(temp_root)/f'retry-{i}.png'
        crop=(x0*72/dpi,(page_height-y1)*72/dpi,(page_width-x1)*72/dpi,y0*72/dpi)
        with closing(pdf_page.render(scale=300/72,crop=crop)) as bitmap:
            bitmap.to_pil().convert('RGB').save(path)
        found=[]
        for result in model().predict(str(path)):
            for word in extract_words(result):
                word.update(left=word['left']/scale+x0,top=word['top']/scale+y0,
                            width=word['width']/scale,height=word['height']/scale,
                            polygon=[[x/scale+x0,y/scale+y0] for x,y in word['polygon']],
                            source='targeted_ocr',retry_kind=region['kind'])
                found.append(word)
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
                candidates.append(word)
            elif kind in {'supplier_name','description'} and re.search(r'[A-Za-z]{3,}',text) and len(text)>8:
                candidates.append(word)
        if kind in {'numeric_cell','vat_identifier','invoice_identifier'} and len(candidates)!=1:
            continue
        if kind=='numeric_cell' and candidates:
            words=[w for w in words if w!=retry['original']]
        for w in candidates:
            # Do not duplicate a successfully read same-language source phrase.
            if any(w['text'].casefold()==old['text'].casefold() and abs(center(w)[1]-center(old)[1])<old.get('height',20) for old in words):continue
            words.append(w);accepted.append(dict(kind=kind,text=w['text'],bbox=[w[k] for k in ('left','top','width','height')]))
    page['words']=words
    page['text']='\n'.join(w['text'] for w in words)
    page['targeted_ocr']={'attempted_regions':len(retries),'accepted':accepted,'alternatives':retries}
    return page
