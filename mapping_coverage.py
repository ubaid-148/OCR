"""Account for OCR text without guessing an unassigned word's semantic role."""
from bbox_grouping import box_geometry


def mapping_coverage(parsed, pages):
    data = parsed.get('data', {})
    evidence = dict(parsed.get('quality', {}).get('field_evidence') or {})
    evidence.update(data.get('field_evidence') or {})
    for index, item in enumerate(data.get('items', [])):
        for key, value in (item.get('field_evidence') or {}).items():
            evidence[f'items[{index}].{key}'] = value
    selected = {}
    for field, entries in evidence.items():
        for entry in entries if isinstance(entries, list) else [entries]:
            if not isinstance(entry, dict) or not entry.get('text'):
                continue
            marker = (entry.get('page', 1), str(entry['text']), tuple(entry.get('bbox') or ()))
            selected.setdefault(marker, []).append(field)
    records = []
    for index, page in enumerate(pages, 1):
        for word in page.get('words', []):
            text = str(word.get('text') or '')
            if not text.strip():
                continue
            number = page.get('page', index)
            bbox = tuple(box_geometry(word))
            fields = selected.get((number, text, bbox), [])
            records.append({'page': number, 'text': text, 'bbox': list(bbox),
                            'fields': fields, 'status': 'source_evidence' if fields else 'unassigned'})
    # Unassigned includes labels/logos as well as missed values. It is not a
    # missing-field count, and an occurrence match is not semantic verification.
    return {'ocr_boxes': len(records), 'assigned_boxes': sum(bool(r['fields']) for r in records),
            'unassigned_boxes': sum(not r['fields'] for r in records), 'records': records}


def attach_mapping_coverage(parsed, pages):
    report = mapping_coverage(parsed, pages)
    parsed['mapping_coverage'] = report
    parsed['unmapped_text'] = [{'page': r['page'], 'text': r['text']}
                               for r in report['records'] if not r['fields']]
    return parsed
