"""Complete structured invoice response; diagnostic OCR evidence stays internal."""

from visual_invoice import ITEM_NUMBERS, ITEM_TEXT, TEXT_FIELDS, TOTAL_NUMBERS, VAT_NUMBERS


def clean_invoice_response(payload):
    data = payload.get('data') or {}
    quality = payload.get('quality') or {}
    def select(value, keys):
        return {key: value.get(key) for key in keys}
    result = select(data, ('source_filename', 'document_type', 'document_type_ar', 'amount_in_words_ar'))
    result['handwritten_notes'] = data.get('handwritten_notes') if 'handwritten_notes' in data else None
    result['supplier'] = select(data.get('supplier') or {}, TEXT_FIELDS['supplier'])
    result['invoice'] = select(data.get('invoice') or {}, TEXT_FIELDS['invoice'])
    result['customer'] = select(data.get('customer') or {}, TEXT_FIELDS['customer'])
    result['items'] = [select(item, ('line_no', *ITEM_TEXT, *ITEM_NUMBERS))
                       for item in data.get('items', [])]
    result['vat_summary'] = select(data.get('vat_summary') or {}, (*VAT_NUMBERS, 'tax_code'))
    result['totals'] = select(data.get('totals') or {}, (*TOTAL_NUMBERS, 'currency'))
    result['other_fields'] = data.get('other_fields') if 'other_fields' in data else None
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
    for issue in quality.get('evidence_issues') or []:
        reasons.append(f"Check field: {issue.get('field', 'unverified value')} — {issue.get('reason', 'source evidence is insufficient')}")
    needs_review = quality.get('needs_review', True)
    if needs_review and not reasons:
        reasons.append('Check the extracted fields and totals against the PDF.')
    response={'status': 'needs_review' if needs_review else 'extracted',
            'pipeline_version': payload.get('pipeline_version', 'unknown'),
            'parser': quality.get('parser', 'unknown'),
            'local_ai_status': quality.get('local_ai_status', 'not_reported'),
            'data': result, 'review_notes': list(dict.fromkeys(reasons))}
    response['ocr_device']=payload.get('ocr_device','unknown')
    response['timings_seconds']=payload.get('timings_seconds',{})
    if quality.get('local_ai_status')=='failed' and quality.get('local_ai_error'):
        response['local_ai_error']=quality['local_ai_error']
    return response
