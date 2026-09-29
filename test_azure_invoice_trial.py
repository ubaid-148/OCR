import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

import pypdfium2 as pdfium

from azure_invoice_trial import analyze_pdf, field_value, summarize, validate_endpoint


def pdf_bytes(pages=1):
    buffer = io.BytesIO()
    with pdfium.PdfDocument.new() as document:
        for _ in range(pages):
            page = document.new_page(100, 100)
            page.close()
        document.save(buffer)
    return buffer.getvalue()


class AzureTrialTests(unittest.TestCase):
    def test_rejects_unconfirmed_tier_before_network(self):
        transport = Mock()
        with self.assertRaisesRegex(ValueError, 'Free F0'):
            analyze_pdf(b'', 'a.pdf', 'https://trial.cognitiveservices.azure.com', 'secret', transport=transport)
        transport.assert_not_called()

    def test_rejects_long_pdf_before_network(self):
        transport = Mock()
        with self.assertRaisesRegex(ValueError, '1–2 page'):
            analyze_pdf(pdf_bytes(3), 'a.pdf', 'https://trial.cognitiveservices.azure.com', 'secret',
                        confirmed_f0=True, transport=transport)
        transport.assert_not_called()

    def test_submit_poll_preserves_zero_and_sources_without_credentials(self):
        endpoint = 'https://trial.cognitiveservices.azure.com'
        raw = {'status': 'succeeded', 'analyzeResult': {'pages': [{'pageNumber': 1}],
            'documents': [{'fields': {'InvoiceTotal': {'type': 'currency', 'valueCurrency': {'amount': 0, 'currencyCode': 'SAR'},
                'content': '0.00', 'confidence': .97, 'boundingRegions': [{'pageNumber': 1, 'polygon': [1, 2]}]}}}]}}
        transport = Mock(side_effect=[(202, {'Operation-Location': endpoint+'/result'}, {}),
                                      (200, {}, {'status': 'running'}), (200, {}, raw)])
        result, original = analyze_pdf(pdf_bytes(), 'first.pdf', endpoint, 'secret', confirmed_f0=True,
                                      transport=transport, sleep=lambda _: None)
        self.assertEqual(original, raw)
        self.assertEqual(result['source_filename'], 'first.pdf')
        self.assertEqual(result['documents'][0]['fields']['InvoiceTotal']['amount'], 0)
        self.assertEqual(result['documents'][0]['field_sources']['InvoiceTotal']['boundingRegions'][0]['pageNumber'], 1)
        self.assertIn('InvoiceId', result['documents'][0]['missing_core_fields'])
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(transport.call_count, 3)
        self.assertIn('pages=1-1', transport.call_args_list[0].args[0])

    def test_poll_does_not_forward_key_to_another_host(self):
        transport = Mock(return_value=(202, {'Operation-Location': 'https://example.com/result'}, {}))
        with self.assertRaisesRegex(RuntimeError, 'unexpected operation host'):
            analyze_pdf(pdf_bytes(), 'a.pdf', 'https://trial.cognitiveservices.azure.com', 'secret',
                        confirmed_f0=True, transport=transport)
        self.assertEqual(transport.call_count, 1)

    def test_rejects_unsafe_endpoint(self):
        for url in ('http://trial.cognitiveservices.azure.com', 'https://example.com',
                    'https://trial.cognitiveservices.azure.com.evil.example',
                    'https://user:pass@trial.cognitiveservices.azure.com'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_endpoint(url)

    def test_item_evidence_and_multiple_invoices_are_preserved(self):
        item = {'type': 'object', 'valueObject': {'Quantity': {'type': 'number', 'valueNumber': 0, 'confidence': .9}}}
        raw = {'analyzeResult': {'documents': [{'fields': {'Items': {'type': 'array', 'valueArray': [item]}}}, {'fields': {}}]}}
        result = summarize(raw, 'two.pdf', 1)
        self.assertEqual(len(result['documents']), 2)
        self.assertEqual(result['documents'][0]['fields']['Items'][0]['Quantity'], 0)
        self.assertIn('Items[0].Quantity', result['documents'][0]['field_sources'])

    def test_notebook_embeds_current_client_and_compiles(self):
        root = Path(__file__).parent
        notebook = json.loads((root/'Azure_Invoice_Free_Trial.ipynb').read_text())
        sources = [''.join(c['source']) for c in notebook['cells'] if c['cell_type'] == 'code']
        self.assertIn((root/'azure_invoice_trial.py').read_text(), sources)
        for source in sources:
            compile(source, 'trial-cell', 'exec')


if __name__ == '__main__':
    unittest.main()
