import json
import unittest
from unittest.mock import patch

from invoice_result import extract_result


class InvoiceResultTests(unittest.TestCase):
    def test_recovered_number_is_not_still_reported_missing(self):
        from local_ai_parser import parse_invoice_hybrid
        from test_label_value_mapping import box
        draft = {'data': {'supplier': {}, 'customer': {}, 'invoice': {}, 'totals': {}, 'items': []},
                 'quality': {'missing_fields': ['invoice.invoice_number'], 'needs_review': True}}
        pages = [{'page': 1, 'words': [box('Inv. No.', 100, 100), box('00042', 190, 100)]}]
        with patch('local_ai_parser._parse_invoice_hybrid', return_value=draft):
            parsed = parse_invoice_hybrid(pages, 'example.pdf', 'eng', mode='fast')
        self.assertEqual(parsed['data']['invoice']['invoice_number'], '00042')
        self.assertNotIn('invoice.invoice_number', parsed['quality']['missing_fields'])
        self.assertIn('invoice.date', parsed['quality']['missing_fields'])

    def test_result_identifies_pdf_and_keeps_audited_locations_in_details(self):
        parsed = {'data': {}, 'quality': {'missing_fields': ['invoice.date']},
                  'mapping_coverage': {'records': [
                      dict(page=1, text='40', bbox=[10, 20, 30, 10], fields=['totals.subtotal']),
                      dict(page=2, text='40', bbox=[10, 20, 30, 10], fields=[])]}}
        details = {}
        with patch('invoice_result.parse_invoice_hybrid', return_value=parsed):
            result = extract_result({'pages': [], 'timings_seconds': {'model_load': 3}}, 'a.pdf', details=details)
        self.assertEqual(result['source_filename'], 'a.pdf')
        self.assertNotIn('field_sources', result)
        self.assertNotIn('unassigned_ocr', result)
        self.assertEqual(details['mapping_coverage']['records'][0]['page'], 1)
        self.assertEqual(result['missing_fields'], ['invoice.date'])
        self.assertEqual(result['timings_seconds']['ocr']['model_load'], 3)

    def test_unmapped_text_is_only_in_details(self):
        parsed={'data':{},'quality':{},'unmapped_text':[{'page':1,'text':'Logo'}], 'mapping_coverage':{'records':[]}}
        details={}
        with patch('invoice_result.parse_invoice_hybrid',return_value=parsed):
            result=extract_result({'pages':[]},'example.pdf',details=details)
        self.assertNotIn('unmapped_text',result)
        self.assertEqual(details['unmapped_text'],parsed['unmapped_text'])
        self.assertIn('mapping_coverage',details)

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
        self.assertEqual(len(result['review_notes']), 4)
        self.assertNotIn('field_evidence', json.dumps(result))
        self.assertNotIn('validation', result)

    def test_optional_header_fields_survive_concise_output(self):
        for cr_key in ("cr_number", "commercial_registration"):
            with self.subTest(cr_key=cr_key):
                parsed = {"data": {
                    "invoice": {"date_of_supply": "2026-03-08", "payment_method": "CASH"},
                    "supplier": {cr_key: "2051065071"},
                    "customer": {"customer_code": "00394", "address": "Khobar",
                                 cr_key: "1234567890"}},
                    "quality": {"needs_review": False}}
                with patch("invoice_result.parse_invoice_hybrid", return_value=parsed):
                    result = extract_result({"pages": []}, "9495.pdf")
                self.assertEqual(result["payment_method"], "CASH")
                self.assertEqual(result["date_of_supply"], "2026-03-08")
                self.assertEqual(result["customer"]["customer_code"], "00394")
                self.assertEqual(result["customer"]["address"], "Khobar")
                self.assertEqual(result["supplier"]["commercial_registration"], "2051065071")
                self.assertEqual(result["customer"]["commercial_registration"], "1234567890")
                self.assertEqual(result["review_notes"], [])

    def test_empty_ocr_is_not_reported_as_success(self):
        result = extract_result({'pages': []}, 'empty.pdf')
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['items'], [])
        self.assertTrue(result['review_notes'])

    def test_derived_amount_keeps_printed_values_and_flags_optional_fields(self):
        parsed = {'data': {
            'invoice': {'invoice_number': 'INV-1', 'date': '2026-09-03', 'date_of_supply': None,
                        'payment_method': None},
            'supplier': {'name_ar': 'Seller', 'name_en': None, 'vat_number': '1',
                         'commercial_registration': None},
            'customer': {'name': 'Buyer', 'vat_number': '2', 'commercial_registration': None,
                         'customer_code': None, 'address': None},
            'items': [{'item_code': 'A', 'description': 'Oil', 'quantity': 2,
                       'unit_price': 10, 'amount': 20, 'vat_amount': 3, 'gross_amount': 23,
                       'printed_amount': 10, 'printed_vat_amount': 1.5,
                       'amount_source': 'derived_quantity_price'}],
            'totals': {'subtotal': 20, 'discount': None, 'vat_rate': 15,
                       'vat_amount': 3, 'net_amount': 23, 'currency': 'SAR'},
            'validation': {'items_calculation_valid': True}},
            'quality': {'needs_review': True, 'missing_fields': []}}
        with patch('invoice_result.parse_invoice_hybrid', return_value=parsed):
            result = extract_result({'pages': []}, 'example.pdf')
        item = result['items'][0]
        self.assertEqual(item['printed_amount'], 10)
        self.assertEqual(item['printed_vat_amount'], 1.5)
        self.assertEqual(item['amount_source'], 'derived_quantity_price')
        self.assertTrue(any(note.startswith('Optional printed fields not included:')
                    for note in result['review_notes']))


if __name__ == '__main__':
    unittest.main()
