"""Extract optional printed details by labels, without inferring handwriting."""
import re
from invoice_formatter import center, contains
from layout_invoice import geometry, proof


def add_printed_details(data, pages):
    evidence=data.setdefault('field_evidence',{})
    for page in pages:
        words=page.get('words',[])
        h,y=geometry(words)
        def labeled(pattern, field, below=False, pool=None):
            pool=words if pool is None else pool
            for label in pool:
                match=re.match(pattern,label['text'],re.I)
                if not match:continue
                tail=label['text'][match.end():].strip(' :')
                value=label
                if not tail:
                    candidates=[w for w in pool if w is not label and
                                ((abs(y(w)-y(label))<h*.65 and w['left']>label['left']+label['width'] and w['left']-label['left']-label['width']<h*5) or
                                 (below and 0<y(w)-y(label)<h*2 and abs(w['left']-label['left'])<h))]
                    value=min(candidates,key=lambda w:(abs(y(w)-y(label)),w['left']),default=None)
                    tail=value['text'].strip(' :') if value else ''
                if tail and value and (value.get('source')=='native_text' or value.get('confidence',0)>=85):
                    evidence[field]=proof(dict(value,_page=page.get('page',1)))
                    return tail
            return None
        address=labeled(r'^CUSTOMER ADDRESS\s*:?', 'customer.address', below=True)
        if address:data['customer']['address']=address
        business=next((w for w in words if re.match(r'(?i)^SALE ALL KINDS OF\b',w['text'])),None)
        if business:
            data['supplier']['business_type']=business['text']
            evidence['supplier.business_type']=proof(dict(business,_page=page.get('page',1)))
        amount_words=labeled(r'^AMOUNT IN WORDS\s*:', 'totals.amount_in_words')
        if amount_words:data['totals']['amount_in_words']=amount_words
        bank_label=next((w for w in words if contains(w['text'],('bank details',))),None)
        if bank_label:
            stop=min((y(w) for w in words if y(w)>y(bank_label) and contains(w['text'],('authorized signature','received by'))),default=float('inf'))
            pool=[w for w in words if y(bank_label)<y(w)<stop and w['left']<bank_label['left']+h*35]
            bank={key:labeled(pattern,'bank_details.'+key,pool=pool) for key,pattern in [
                ('beneficiary',r'^Beneficiary\s*:'),('bank_name',r'^Bank\s*:?(?=\s|$)'),
                ('account_no',r'^A/c\s*No\.?\s*:?'),('branch',r'^Branch\s*:?'),('iban',r'^IBAN\s*:?')]}
            if any(bank.values()):data['bank_details']=bank
    return data
