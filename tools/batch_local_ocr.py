"""Fresh free/local OCR of sample PDFs in one process, with resumable results.

Run using the prepared OCR Python. Recognition models are reused between PDFs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from invoice_result import extract_result


def code_fingerprint():
    root = Path(__file__).resolve().parents[1]
    value = hashlib.sha256()
    for path in sorted(root.glob('*.py')):
        value.update(path.name.encode()); value.update(path.read_bytes())
    value.update((root/'requirements.txt').read_bytes())
    value.update(json.dumps({k:v for k,v in os.environ.items() if k.startswith(('OCR_', 'PADDLE_', 'OLLAMA_', 'VISION_')) or k == 'USE_LOCAL_AI'}, sort_keys=True).encode())
    return value.hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    temporary.replace(path)


def run_batch(pdf_dir, output, extractor=None, *, mode="auto", language="eng+ara"):
    if mode not in {"auto", "fast"}:
        raise ValueError("Extraction mode must be auto or fast")
    if extractor is None:
        from coordinate_ocr import extract_pdf
        extractor = extract_pdf
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    sources = sorted(path for path in Path(pdf_dir).iterdir()
                     if path.is_file() and path.suffix.lower() == '.pdf')
    if not sources:
        raise ValueError('No PDFs found')
    fingerprint = code_fingerprint()
    rows = []
    for index, source in enumerate(sources, 1):
        signature = {'pdf_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'code': fingerprint, 'mode': mode, 'language': language}
        result_path = output / (source.stem+'.json')
        cached = None
        if result_path.exists():
            try:
                cached = json.loads(result_path.read_text())
            except ValueError:
                pass
        if cached and isinstance(cached, dict) and cached.get('signature') == signature and cached.get('complete') and not cached.get('retryable'):
            record = cached
            print(f'{index}/{len(sources)} {source.name}: reusing completed result', flush=True)
        else:
            started = perf_counter()
            print(f'{index}/{len(sources)} {source.name}: OCR starting', flush=True)
            try:
                raw = extractor(source, language, progress=lambda message: print('  '+message, flush=True))
                save(output/(source.stem+'.ocr.json'), raw)
                details = {}
                result = extract_result(raw, source.name, language, pdf_path=source, mode=mode, details=details)
                save(output/(source.stem+'.details.json'), details)
                record = {'file': source.name, 'signature': signature, 'complete': True,
                          'seconds': round(perf_counter()-started, 3), 'result': result,
                          'retryable': details['quality'].get('local_ai_status') in {'failed', 'partial_failure'} or bool(details.get('vision_page_errors')),
                          'quality': details['quality'], 'checks': details['data'].get('validation', {})}
            except Exception as error:
                record = {'file': source.name, 'signature': signature, 'complete': False,
                          'error': f'{type(error).__name__}: {error}'}
            save(result_path, record)
        rows.append(record)
        summary = {'scope': 'Fresh OCR health checks; not ground-truth accuracy.',
                   'pdfs_total': len(sources), 'pdfs_processed': len(rows),
                   'completed': sum(r.get('complete', False) for r in rows),
                   'errors': sum(not r.get('complete') for r in rows),
                   'needs_review': sum(r.get('result', {}).get('status') == 'needs_review' for r in rows),
                   'files': [{'file': r['file'], 'complete': r['complete'],
                              'missing_fields': r.get('quality', {}).get('missing_fields', []),
                              'error': r.get('error')} for r in rows]}
        save(output/'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf-dir', type=Path, default=Path('public_invoice_pdfs'))
    parser.add_argument('--mode', choices=('auto', 'fast'), default='auto',
                        help='auto reads original page images with local vision; fast uses only OCR rules')
    parser.add_argument('--language', default='eng+ara')
    parser.add_argument('--output', type=Path, default=Path('benchmark_outputs/live-local'))
    args = parser.parse_args()
    result = run_batch(args.pdf_dir, args.output, mode=args.mode, language=args.language)
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}, indent=2))
