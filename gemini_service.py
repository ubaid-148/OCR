"""Server-side Gemini connection; never expose provider exceptions or API keys."""
import os
import re

from tools.test_gemini_invoice import DEFAULT_MODEL, extract


class GeminiServiceError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def client_options(**kwargs):
    from google.genai import types
    # The parent SDK normalizes attempts=0 to 1, while Interactions treats 1
    # as an extra retry. Explicitly exclude 429 so Retry-After cannot stall
    # quota errors. Only transient server failures remain retryable.
    return types.HttpOptions(timeout=180_000, retry_options=types.HttpRetryOptions(
        attempts=1, http_status_codes=[500, 502, 503, 504]), **kwargs)


def safe_error_message(error, key):
    # Interactions errors expose status_code; generateContent errors use code.
    code = getattr(error, 'status_code', None) or getattr(error, 'code', None)
    hints = {400: 'Gemini rejected the request.', 401: 'API key authentication failed.',
             403: 'API key/project access was denied.',
             404: 'The configured Gemini model is unavailable. Set GEMINI_MODEL to a model available in your AI Studio project.',
             429: 'Gemini quota/rate limit reached. Check your Free-tier model quota in AI Studio.',
             500: 'Google reported an internal service error.',
             503: 'Gemini is temporarily unavailable.'}
    body = getattr(error, 'body', None)
    detail = body.get('error', body) if isinstance(body, dict) else {}
    message = detail.get('message') if isinstance(detail, dict) else None
    if code and isinstance(message, str):
        # Only the provider error message, never request headers/body or the
        # exception repr (schema validation errors can include invoice content).
        if key:
            message = message.replace(key, '[redacted]')
        message = re.sub(r'AIza[\w-]{20,}', '[redacted]', message)
        message = re.sub(r'(?i)((?:key|token|authorization)\s*[=:]\s*)[^\s&,;]+', r'\1[redacted]', message)
        message = ' '.join(message.split())[:700]
        return f'Gemini HTTP {code}: {hints.get(code, "Request failed.")} {message}'
    if code:
        return f'Gemini HTTP {code}: {hints.get(code, "Request failed.")}'
    kind = type(error).__name__
    if kind in {'ValidationError', 'JSONDecodeError'}:
        return f'Gemini returned invalid or incomplete invoice JSON ({kind}). No values were accepted.'
    if kind in {'ConnectError', 'APIConnectionError', 'TimeoutException', 'ReadTimeout', 'APITimeoutError'}:
        return f'Gemini connection failed ({kind}). Check internet access; no invoice result was received.'
    return f'Gemini client failed ({kind}). No invoice result was accepted; check SDK configuration.'


def extract_gemini(pdf, filename):
    from gemini_config import load_gemini_env
    load_gemini_env()
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        raise ValueError('Gemini key is missing. Add GEMINI_API_KEY to OCR/.env or start run_gemini_web.py and enter it in the terminal.')
    if not pdf.startswith(b'%PDF-') or len(pdf) > 10_000_000:
        raise ValueError('Gemini direct upload currently accepts PDFs up to 10 MB.')
    try:
        from google import genai
        with genai.Client(api_key=key, http_options=client_options()) as client:
            return extract(pdf, filename, client, os.environ.get('GEMINI_MODEL', DEFAULT_MODEL))
    except Exception as error:
        raise GeminiServiceError(safe_error_message(error, key),
                                 getattr(error, 'status_code', None) or getattr(error, 'code', None)) from None
