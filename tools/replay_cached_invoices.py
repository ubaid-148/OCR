"""Replay saved OCR through the free spatial parser; not a recognition benchmark."""
import argparse
from collections import Counter
import json
from pathlib import Path
from time import perf_counter
from invoice_result import extract_result


def replay(raw_dir):
    rows = []
    for path in sorted(Path(raw_dir).glob('*.json')):
        started = perf_counter()
        try:
            details = {}
            result = extract_result(json.loads(path.read_text()), path.stem, mode='fast', details=details)
            rows.append({'file': path.name, 'seconds': round(perf_counter()-started, 4),
                         'invoice_number': result['invoice_number'], 'items': len(result['items']),
                         'supplier_cr': result['supplier']['commercial_registration'],
                         'status': result['status'], 'missing': details['quality'].get('missing_fields', []),
                         'checks': details['data'].get('validation', {})})
        except Exception as error:
            rows.append({'file': path.name, 'error': f'{type(error).__name__}: {error}'})
    if not rows:
        raise ValueError('No cached OCR JSON files found')
    return {'scope': 'Cached OCR parser replay only. No fresh recognition or ground-truth accuracy measurement.',
            'samples': len(rows), 'errors': sum('error' in row for row in rows),
            'invoice_numbers_present': sum(bool(row.get('invoice_number')) for row in rows),
            'supplier_cr_present': sum(bool(row.get('supplier_cr')) for row in rows),
            'three_financial_checks_passed': sum(all(row.get('checks', {}).get(k) is True for k in
                ('items_calculation_valid', 'subtotal_valid', 'net_amount_valid')) for row in rows),
            'missing_field_counts': dict(Counter(k for row in rows for k in row.get('missing', []))),
            'results': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw_dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.raw_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('results', 'missing_field_counts')}, indent=2))
