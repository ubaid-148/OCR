"""Reproducible cached-OCR verification; never a live OCR accuracy benchmark."""
import argparse
import json
from pathlib import Path
from invoice_result import extract_result
from invoice_response import clean_invoice_response, validate_response_schema
from tools.batch_local_ocr import save, code_fingerprint


def verify(raw_dir, output, source_checks=None):
    output = Path(output)
    if output.resolve() == Path(raw_dir).resolve():
        raise ValueError('Verification output must differ from the raw OCR directory')
    output.mkdir(parents=True, exist_ok=True)
    sources = sorted(Path(raw_dir).glob('*.json'))
    if not sources:
        raise ValueError('No cached OCR files found')
    rows, results = [], {}
    for path in sources:
        try:
            details = {}
            result = extract_result(json.loads(path.read_text(encoding='utf-8')),
                                    path.stem, mode='fast', details=details)
            validate_response_schema(clean_invoice_response(details))
            save(output/path.name, {'result': result, 'details': details})
            results[path.stem] = result
            rows.append({'file': path.stem, 'items': len(result['items']),
                         'missing': details['quality'].get('missing_fields', []),
                         'unmapped_text': len(result['unmapped_text']), 'schema_valid': True})
        except Exception as error:
            rows.append({'file': path.stem, 'error': f'{type(error).__name__}: {error}'})
    checks = []
    if source_checks:
        for check in json.loads(Path(source_checks).read_text(encoding='utf-8'))['checks']:
            actual = results.get(check['file'])
            for part in check['field'].split('.'):
                if isinstance(actual, list) and part.isdecimal():
                    actual = actual[int(part)] if int(part) < len(actual) else None
                else:
                    actual = actual.get(part) if isinstance(actual, dict) else None
            checks.append(dict(check, actual=actual, match=actual == check['expected']))
    summary = {'scope': 'Current code on cached OCR; not fresh Paddle or vision inference.',
               'code_fingerprint': code_fingerprint(), 'documents': len(rows),
               'errors': sum('error' in r for r in rows),
               'schema_valid': sum(r.get('schema_valid', False) for r in rows),
               'documents_with_items': sum(bool(r.get('items')) for r in rows),
               'source_checks_matched': sum(c['match'] for c in checks),
               'source_checks_total': len(checks), 'source_checks': checks, 'files': rows}
    save(output/'summary.json', summary)
    return summary


def verification_failed(summary):
    """A clean parser run is not a pass when supplied source checks disagree."""
    return bool(summary['errors'] or
                summary['source_checks_matched'] != summary['source_checks_total'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw_dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-checks', type=Path)
    args = parser.parse_args()
    summary = verify(args.raw_dir, args.output, args.source_checks)
    print(json.dumps({k:v for k,v in summary.items() if k not in {'files','source_checks'}}, indent=2))
    raise SystemExit(1 if verification_failed(summary) else 0)
