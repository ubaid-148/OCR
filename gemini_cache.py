"""Keep completed Gemini results before attempting to deliver them to a browser."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from gemini_config import load_gemini_env
from tools.test_gemini_invoice import DEFAULT_MODEL, PROMPT, extraction_schema

CACHE_DIR = Path(__file__).resolve().parent / 'benchmark_outputs' / 'gemini-web'


def cached_extraction(pdf, filename, extractor):
    load_gemini_env()
    config = json.dumps({'model': os.environ.get('GEMINI_MODEL', DEFAULT_MODEL),
                         'prompt': PROMPT, 'schema': extraction_schema(), 'version': 1}, sort_keys=True)
    digest = hashlib.sha256(pdf + config.encode()).hexdigest()
    path = CACHE_DIR / (digest + '.json')
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        if (isinstance(result, dict) and result.get('schema_version') == 'gemini-trial-1'
                and result.get('status') == 'needs_review' and result.get('invoices')):
            return dict(result, source_filename=filename, cache_hit=True), path
    except (OSError, ValueError):
        pass
    result = extractor(pdf, filename)
    result = dict(result, schema_version='gemini-trial-1', cache_hit=False)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=CACHE_DIR,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return result, path
