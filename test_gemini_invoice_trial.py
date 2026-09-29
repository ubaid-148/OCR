import json
import io
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.test_gemini_invoice import extract, extraction_schema


def empty_value(schema):
    kind = schema['type']
    if isinstance(kind, list) and 'null' in kind:
        return None
    if kind == 'object':
        return {key: empty_value(value) for key, value in schema['properties'].items()}
    if kind == 'array':
        return []
    if kind == 'integer':
        return 1
    return ''


class GeminiTrialTests(unittest.TestCase):
    def test_quota_error_is_not_retried_even_with_retry_after(self):
        import httpx
        from google import genai
        from gemini_service import client_options
        requests = []
        def quota(request):
            requests.append(request)
            return httpx.Response(429, headers={'Retry-After': '0'}, json={
                'error': {'code': 429, 'message': 'Daily free quota exhausted'}})
        with genai.Client(api_key='test', http_options=client_options(
                client_args={'transport': httpx.MockTransport(quota)})) as client:
            with self.assertRaises(Exception) as caught:
                client.interactions.create(model='test', input='test')
        self.assertEqual(getattr(caught.exception, 'status_code', None), 429)
        self.assertEqual(len(requests), 1)

    def client(self, value, status='completed'):
        return SimpleNamespace(interactions=SimpleNamespace(create=Mock(return_value=
            SimpleNamespace(status=status, output_text=json.dumps(value)))))

    def test_pdf_schema_and_separate_invoices(self):
        entry = empty_value(extraction_schema()['properties']['invoices']['items'])
        entry['page_numbers'] = [1]
        entry['data']['invoice']['invoice_number'] = '00042'
        entry['data']['totals']['discount'] = 0
        client = self.client({'invoices': [entry, entry]})
        result = extract(b'%PDF-example', 'a.pdf', client)
        self.assertEqual(result['source_filename'], 'a.pdf')
        self.assertEqual(len(result['invoices']), 2)
        self.assertEqual(result['invoices'][0]['data']['invoice']['invoice_number'], '00042')
        self.assertEqual(result['invoices'][0]['data']['totals']['discount'], 0)
        request = client.interactions.create.call_args.kwargs
        self.assertFalse(request['store'])
        self.assertEqual(request['input'][0]['mime_type'], 'application/pdf')
        self.assertEqual(result['status'], 'needs_review')

    def test_rejects_partial_or_invalid_schema(self):
        from jsonschema import ValidationError
        with self.assertRaises(ValueError):
            extract(b'%PDF-example', 'a.pdf', self.client({'invoices': []}, status='incomplete'))
        with self.assertRaises(ValidationError):
            extract(b'%PDF-example', 'a.pdf', self.client({'invoices': [{'data': {}}]}))

    def test_invalid_pdf_never_calls_provider(self):
        client = self.client({})
        with self.assertRaises(ValueError):
            extract(b'not pdf', 'a.pdf', client)
        client.interactions.create.assert_not_called()

    def test_web_gemini_bypasses_local_ocr(self):
        from ocr_web import Handler
        body = (b'--test\r\nContent-Disposition: form-data; name="mode"\r\n\r\ngemini\r\n'
                b'--test\r\nContent-Disposition: form-data; name="pdf"; filename="sample.pdf"\r\n'
                b'Content-Type: application/pdf\r\n\r\n%PDF-example\r\n--test--\r\n')
        handler = object.__new__(Handler)
        handler.path = '/'
        handler.headers = {'Content-Type': 'multipart/form-data; boundary=test', 'Content-Length': str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.log_message = Mock()
        expected = {'status': 'needs_review', 'source_filename': 'sample.pdf', 'invoices': []}
        with tempfile.TemporaryDirectory() as temporary, \
             patch('ocr_web.UPLOAD_DIR', Path(temporary)), \
             patch('gemini_cache.CACHE_DIR', Path(temporary) / 'results'), \
             patch('gemini_service.extract_gemini', return_value=expected) as api, \
             patch('ocr_web.subprocess.run') as local_process, \
             patch('ocr_web.parse_invoice_visual') as local_ai, \
             patch.object(handler, 'send_json') as send:
            handler.process_upload()
            api.assert_called_once_with(b'%PDF-example', 'sample.pdf')
            local_process.assert_not_called()
            local_ai.assert_not_called()
            self.assertEqual(send.call_args.args[0]['schema_version'], 'gemini-trial-1')
            self.assertEqual([p for p in Path(temporary).iterdir() if p.is_file()], [])

    def test_key_not_rendered_in_web_page(self):
        from ocr_web import page
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'secret-test-key', 'OCR_DEFAULT_MODE': 'gemini'}):
            html = page().decode()
        self.assertNotIn('secret-test-key', html)
        self.assertIn('value="gemini" selected', html)

    def test_result_survives_disconnect_and_repeat_avoids_api(self):
        from gemini_cache import cached_extraction
        from ocr_web import Handler
        result = {'status': 'needs_review', 'invoices': [{'data': {'invoice': {'invoice_number': '0042'}}}],
                  'seconds': 106, 'source_filename': 'first.pdf'}
        api = Mock(return_value=result)
        with tempfile.TemporaryDirectory() as temporary, patch('gemini_cache.CACHE_DIR', Path(temporary)):
            first, path = cached_extraction(b'%PDF-sample', 'first.pdf', api)
            handler = object.__new__(Handler)
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock(side_effect=BrokenPipeError())
            handler.log_message = Mock()
            self.assertFalse(handler.send_json(first))
            self.assertTrue(path.exists())
            again, _ = cached_extraction(b'%PDF-sample', 'renamed.pdf', api)
            self.assertTrue(again['cache_hit'])
            self.assertEqual(again['source_filename'], 'renamed.pdf')
            self.assertEqual(again['seconds'], 106)
            api.assert_called_once()
            cached_extraction(b'%PDF-different', 'renamed.pdf', api)
            self.assertEqual(api.call_count, 2)

    def test_failed_extraction_is_not_cached(self):
        from gemini_cache import cached_extraction
        with tempfile.TemporaryDirectory() as temporary, patch('gemini_cache.CACHE_DIR', Path(temporary)):
            with self.assertRaises(RuntimeError):
                cached_extraction(b'%PDF-sample', 'a.pdf', Mock(side_effect=RuntimeError('quota')))
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_provider_error_does_not_expose_secret(self):
        from gemini_service import extract_gemini
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'secret-test-key'}), \
             patch('google.genai.Client', side_effect=RuntimeError('secret-test-key')):
            with self.assertRaises(RuntimeError) as raised:
                extract_gemini(b'%PDF-example', 'a.pdf')
        self.assertNotIn('secret-test-key', str(raised.exception))

    def test_real_sdk_error_exposes_http_code_and_redacted_provider_reason(self):
        import httpx
        from google import genai
        from google.genai import types
        from gemini_service import safe_error_message
        key = 'secret-test-key'
        for status in (400, 403, 404, 429):
            with self.subTest(status=status):
                transport = httpx.MockTransport(lambda request: httpx.Response(status, json={
                    'error': {'code': status, 'message': 'Model/quota diagnostic; key=' + key}}))
                with genai.Client(api_key=key, http_options=types.HttpOptions(
                        client_args={'transport': transport})) as client:
                    try:
                        client.interactions.create(model='test', input='test')
                    except Exception as error:
                        message = safe_error_message(error, key)
                    else:
                        self.fail('Expected SDK HTTP error')
                self.assertIn(f'HTTP {status}', message)
                self.assertIn('Model/quota diagnostic', message)
                self.assertNotIn(key, message)


if __name__ == '__main__':
    unittest.main()
