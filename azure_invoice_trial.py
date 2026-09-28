"""Small, standalone Azure F0 invoice trial; no Paddle or GPU required."""
import base64
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

API_VERSION = '2024-11-30'


def validate_endpoint(endpoint):
    endpoint = endpoint.strip().rstrip('/')
    parts = urlsplit(endpoint)
    if (parts.scheme != 'https' or not parts.hostname or
            not parts.hostname.endswith('.cognitiveservices.azure.com') or
            parts.username or parts.password or parts.port not in (None, 443) or
            parts.path or parts.query or parts.fragment):
        raise ValueError('Use the HTTPS endpoint from your Azure Document Intelligence resource.')
    return endpoint


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_json(url, key, body=None, timeout=30):
    headers = {'Ocp-Apim-Subscription-Key': key, 'Content-Type': 'application/json'}
    request = Request(url, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers, method='POST' if body is not None else 'GET')
    try:
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            content = response.read()
            return response.status, dict(response.headers), json.loads(content) if content else {}
    except HTTPError as error:
        # Do not print headers, credentials or the uploaded document.
        hints = {401: 'Check the key and endpoint.', 403: 'Check resource access.',
                 429: 'Free-tier rate or quota limit reached. Wait before retrying.'}
        raise RuntimeError(f'Azure HTTP {error.code}. ' + hints.get(error.code, 'Check the resource in Azure Portal.')) from None
    except (URLError, TimeoutError):
        raise RuntimeError('Azure connection failed or timed out. No automatic resubmission was made.') from None


def field_value(field):
    kind = field.get('type', '')
    if kind == 'array':
        return [field_value(value) for value in field.get('valueArray', [])]
    if kind == 'object':
        return {name: field_value(value) for name, value in field.get('valueObject', {}).items()}
    key = 'value' + kind[:1].upper() + kind[1:]
    return field.get(key, field.get('content'))


def summarize(raw, filename, seconds):
    analyzed = raw.get('analyzeResult', {})
    documents = []
    for document in analyzed.get('documents', []):
        fields = document.get('fields', {})
        sources = {}

        def collect(name, field):
            sources[name] = {key: field.get(key) for key in ('content', 'confidence', 'boundingRegions')}
            for key, value in field.get('valueObject', {}).items():
                collect(f'{name}.{key}', value)
            for index, value in enumerate(field.get('valueArray', [])):
                collect(f'{name}[{index}]', value)

        for name, field in fields.items():
            collect(name, field)
        values = {name: field_value(field) for name, field in fields.items()}
        documents.append({'fields': values, 'field_sources': sources,
                          'missing_core_fields': [name for name in ('InvoiceId', 'InvoiceDate', 'VendorName', 'InvoiceTotal', 'Items')
                                                  if values.get(name) in (None, '', [])]})
    return {'source_filename': filename, 'provider': 'azure-prebuilt-invoice',
            'api_version': API_VERSION, 'seconds': round(seconds, 3),
            'status': 'needs_review',
            'review_note': 'API output has not been checked against the PDF. Confidence is not measured accuracy.',
            'pages_analyzed': [page['pageNumber'] for page in analyzed.get('pages', [])],
            'documents': documents}


def analyze_pdf(pdf, filename, endpoint, key, *, confirmed_f0=False, timeout=180,
                transport=request_json, sleep=time.sleep, clock=time.monotonic):
    if not confirmed_f0:
        raise ValueError('Confirm that the resource Pricing tier is Free F0 in Azure Portal first. The API key cannot verify its tier.')
    endpoint = validate_endpoint(endpoint)
    if not key.strip():
        raise ValueError('An Azure resource key is required.')
    if not pdf.startswith(b'%PDF-') or len(pdf) > 4_000_000:
        raise ValueError('Choose a valid PDF under 4 MB for this free trial.')
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(pdf) as document:
        pages = len(document)
    if pages not in (1, 2):
        raise ValueError('Choose a 1–2 page PDF for F0. Longer PDFs are rejected to avoid silently missing later pages.')
    started = clock()
    url = (endpoint + '/documentintelligence/documentModels/prebuilt-invoice:analyze'
           f'?api-version={API_VERSION}&pages=1-{pages}')
    status, headers, _ = transport(url, key, {'base64Source': base64.b64encode(pdf).decode()}, timeout=min(30, timeout))
    location = next((v for k, v in headers.items() if k.lower() == 'operation-location'), '')
    if status != 202 or not location:
        raise RuntimeError('Azure did not return an analysis operation.')
    destination, origin = urlsplit(location), urlsplit(endpoint)
    if (destination.scheme, destination.netloc) != (origin.scheme, origin.netloc):
        raise RuntimeError('Azure returned an unexpected operation host; polling stopped.')
    while clock() - started < timeout:
        sleep(2)  # F0 accepts one GET per second; do not aggressively poll.
        remaining = timeout - (clock() - started)
        if remaining <= 0:
            break
        _, _, raw = transport(location, key, timeout=min(30, remaining))
        if raw.get('status') == 'succeeded':
            return summarize(raw, filename, clock() - started), raw
        if raw.get('status') in ('failed', 'canceled'):
            raise RuntimeError('Azure analysis failed. Check the PDF and resource in Azure Portal.')
    raise TimeoutError('Analysis timed out; it may still finish in Azure. No automatic resubmission was made.')
