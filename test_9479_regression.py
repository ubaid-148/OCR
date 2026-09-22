"""Regression on cached OCR: recognition errors must remain visible for review."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from invoice_result import extract_result


class BassamInvoiceTests(unittest.TestCase):
    def test_label_number_cr_and_payment_are_source_bound(self):
        payload = json.loads(Path('tests/fixtures/9479_ocr.json').read_text())
        result = extract_result(payload, '9479.pdf')
        self.assertEqual(result['invoice_number'], '175583')
        self.assertEqual(result['supplier']['commercial_registration'], '2060058760')
        self.assertIsNone(result['payment_method'])  # Credit Day is a label, not a payment.
        self.assertEqual(result['totals']['net_amount'], 122.08)
        self.assertEqual([r['gross_amount'] for r in result['items']], [54.05, 68.03])
        self.assertEqual(result['status'], 'needs_review')
        self.assertTrue(any('VAT sum' in n for n in result['review_notes']))
        # OCR read 17.05 instead of 7.05. Never silently claim it was corrected.
        self.assertEqual(result['items'][0]['vat_amount'], 17.05)

    def test_additional_printed_fields_survive_final_output(self):
        data = {'invoice': {'time': '09:03:38', 'ref_no': 'PO-3'},
                'supplier': {'branch': 'Khobar', 'building_no': '1234'},
                'customer': {'name_ar': 'العميل'},
                'items': [{'unit': 'BAG', 'tax_rate': 15, 'discount': 0}],
                'handwritten_notes': ['Uninterpreted annotation'],
                'other_fields': [{'label': 'Salesman', 'value': 'A', 'page': 1}],
                'bank_details': {'iban': 'TEST'},
                'totals': {'other_charges': 0, 'amount_in_words': 'Test amount'}}
        with patch('invoice_result.parse_invoice_hybrid', return_value={'data': data, 'quality': {}}):
            result = extract_result({'pages': []}, 'sample.pdf')
        self.assertEqual(result['invoice']['time'], '09:03:38')
        self.assertEqual(result['supplier']['building_no'], '1234')
        self.assertEqual(result['items'][0]['unit'], 'BAG')
        self.assertEqual(result['items'][0]['tax_rate'], 15)
        self.assertEqual(result['other_fields'], data['other_fields'])
        self.assertEqual(result['handwritten_notes'], data['handwritten_notes'])
        self.assertEqual(result['bank_details'], data['bank_details'])
        self.assertEqual(result['totals']['other_charges'], 0)


if __name__ == '__main__':
    unittest.main()
