"""Small user-facing invoice response; diagnostic evidence stays internal."""


def clean_invoice_response(payload):
    data = payload.get('data') or {}
    quality = payload.get('quality') or {}
    def select(value, keys):
        return {key: value.get(key) for key in keys}
    result = select(data, ('source_filename',))
    result['supplier'] = select(data.get('supplier') or {}, ('name_ar', 'name_en', 'vat_number'))
    result['invoice'] = select(data.get('invoice') or {}, ('invoice_number', 'date', 'time', 'payment_method'))
    result['customer'] = select(data.get('customer') or {}, ('name', 'vat_number', 'address'))
    result['items'] = [select(item, ('item_code', 'description', 'quantity', 'unit', 'unit_price', 'amount', 'vat_amount', 'gross_amount'))
                       for item in data.get('items', [])]
    result['totals'] = select(data.get('totals') or {}, ('subtotal', 'discount', 'vat_rate', 'vat_amount', 'net_amount', 'currency'))
    if data.get('supplier',{}).get('business_type'):
        result['supplier']['business_type']=data['supplier']['business_type']
    if data.get('totals',{}).get('amount_in_words'):
        result['totals']['amount_in_words']=data['totals']['amount_in_words']
    if data.get('bank_details'):
        result['bank_details']=data['bank_details']
    reasons = list(quality.get('review_reasons') or [])
    for field in quality.get('missing_fields') or []:
        reasons.append(f'Missing field: {field}')
    for field in quality.get('low_confidence_fields') or []:
        reasons.append(f"Check field: {field.get('field', 'unreadable text')}")
    needs_review = quality.get('needs_review', True)
    if needs_review and not reasons:
        reasons.append('Check the extracted fields and totals against the PDF.')
    return {'status': 'needs_review' if needs_review else 'extracted',
            'data': result, 'review_notes': list(dict.fromkeys(reasons))}
