import json
import unittest
from unittest.mock import patch

from invoice_result import extract_result


class InvoiceResultTests(unittest.TestCase):
    def test_concise_output_preserves_review_and_zero_values(self):
        parsed = {'data': {'items': [{'description': 'Paint', 'quantity': None, 'unit_price': 90,
                                     'amount': 180, 'field_evidence': {'bbox': [1,2,3,4]}}],
                           'totals': {'discount': 0, 'net_amount': 207},
                           'validation': {'items_calculation_valid': False}},
                  'quality': {'needs_review': True, 'missing_fields': ['items[0].quantity']*20,
                              'low_confidence_fields': [{'field': 'items[0].description'}]*20}}
        with patch('invoice_result.parse_invoice_hybrid', return_value=parsed):
            result = extract_result({'pages': []}, 'example.pdf')
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['totals']['discount'], 0)
        self.assertIsNone(result['items'][0]['quantity'])
        self.assertEqual(len(result['review_notes']), 3)
        self.assertNotIn('field_evidence', json.dumps(result))
        self.assertNotIn('validation', result)

    def test_empty_ocr_is_not_reported_as_success(self):
        result = extract_result({'pages': []}, 'empty.pdf')
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['items'], [])
        self.assertTrue(result['review_notes'])


if __name__ == '__main__':
    unittest.main()
