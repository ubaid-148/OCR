"""Optional source values must survive the image-first JSON path."""
import os
import unittest
from unittest.mock import patch
from invoice_response import clean_invoice_response
from test_visual_invoice import visual_raw, fallback
from visual_invoice import (FULL_SCHEMA, normalize_full, merge_pages,
                            reconcile_with_spatial, parse_invoice_visual)


class VisualOptionalFieldsTests(unittest.TestCase):
    def test_schema_and_normalization_preserve_printed_optional_fields(self):
        raw = visual_raw()
        raw['supplier'].update(address='Warehouse 4', business_type='Trading')
        raw['bank_details'] = {'account_no': '000123', 'iban': 'SA00123'}
        raw['totals']['amount_in_words'] = 'One hundred nine'
        data = normalize_full(raw, 'invoice.pdf', 'eng')
        self.assertIn('bank_details', FULL_SCHEMA['properties'])
        self.assertIn('amount_in_words', FULL_SCHEMA['properties']['totals']['properties'])
        response = clean_invoice_response({'data': data, 'quality': {'needs_review': True}})
        self.assertEqual(response['data']['bank_details']['account_no'], '000123')
        self.assertEqual(response['data']['supplier']['address'], 'Warehouse 4')
        self.assertEqual(response['data']['supplier']['business_type'], 'Trading')
        self.assertEqual(response['data']['totals']['amount_in_words'], 'One hundred nine')

    def test_later_page_bank_details_survive_and_conflicts_are_flagged(self):
        first = normalize_full(visual_raw(), 'invoice.pdf', 'eng')
        second = normalize_full(visual_raw(), 'invoice.pdf', 'eng', 2)
        second['bank_details']['account_no'] = '000123'
        result, _ = merge_pages([first, second])
        self.assertEqual(result['bank_details']['account_no'], '000123')
        first['bank_details']['account_no'] = '999'
        result, notes = merge_pages([first, second])
        self.assertEqual(result['bank_details']['account_no'], '999')
        self.assertTrue(any('bank_details.account_no' in note for note in notes))

    def test_spatial_optional_fields_fill_gaps_without_overwriting_conflicts(self):
        data = normalize_full(visual_raw(), 'invoice.pdf', 'eng')
        spatial = normalize_full(visual_raw(), 'invoice.pdf', 'eng')
        spatial['bank_details']['account_no'] = '000123'
        spatial['supplier']['address'] = 'OCR address'
        data['supplier']['address'] = 'Vision address'
        spatial['other_fields'].append({'label': 'Dispatch', 'value': '009', 'page': 1})
        notes = reconcile_with_spatial(data, spatial)
        self.assertEqual(data['bank_details']['account_no'], '000123')
        self.assertEqual(data['supplier']['address'], 'Vision address')
        self.assertTrue(any('disagree on supplier.address' in note for note in notes))
        self.assertIn(spatial['other_fields'][-1], data['other_fields'])

    def test_auto_result_retains_ocr_bank_and_unknown_reference(self):
        spatial = fallback()
        spatial['data']['bank_details'] = {'account_no': '000123'}
        spatial['data']['other_fields'] = [{'label': 'Dispatch', 'value': '009', 'page': 1}]
        with patch.dict(os.environ, {'USE_LOCAL_AI': 'true'}), \
             patch('visual_invoice.parse_invoice_hybrid', return_value=spatial), \
             patch('visual_invoice.render_pages', return_value=[(1, 1, ['IMAGE'])]), \
             patch('visual_invoice.ask_visual', return_value=visual_raw()):
            result = parse_invoice_visual('missing-original.pdf', [{'page': 1, 'words': []}], 'invoice.pdf', 'eng')
        self.assertIn('raw_visual_candidate', result)
        response = clean_invoice_response(result)
        self.assertEqual(response['data']['bank_details']['account_no'], '000123')
        self.assertIn(spatial['data']['other_fields'][0], response['data']['other_fields'])
