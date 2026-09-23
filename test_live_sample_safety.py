import unittest
from copy import deepcopy
from test_visual_invoice import visual_raw
from visual_invoice import normalize_full, reconcile_with_spatial


class LiveSampleSafetyTests(unittest.TestCase):
    def pair(self):
        data = normalize_full(visual_raw(), 'unseen.pdf', 'eng+ara')
        return data, deepcopy(data)

    def test_positioned_date_survives_conflicting_handwritten_reading(self):
        data, spatial = self.pair()
        data['invoice']['date'] = '2026-04-02'
        spatial['invoice']['date'] = '11/03/2026'
        spatial['field_evidence'] = {'invoice.date': {'text': '11/03/2026', 'page': 1}}
        notes = reconcile_with_spatial(data, spatial)
        self.assertEqual(data['invoice']['date'], '2026-03-11')
        self.assertTrue(any('handwriting' in n for n in notes))

    def test_absent_arabic_supplier_name_survives_vision(self):
        data, spatial = self.pair()
        data['supplier']['name_ar'] = None
        reconcile_with_spatial(data, spatial)
        self.assertEqual(data['supplier']['name_ar'], spatial['supplier']['name_ar'])

    def test_blank_optional_cell_is_not_zero(self):
        data, spatial = self.pair()
        data['items'][0]['vat_amount'] = 0
        spatial['items'][0]['vat_amount'] = None
        reconcile_with_spatial(data, spatial)
        self.assertIsNone(data['items'][0]['vat_amount'])

    def test_printed_zero_is_retained(self):
        data, spatial = self.pair()
        data['items'][0]['vat_amount'] = 0
        spatial['items'][0]['vat_amount'] = 0
        spatial['items'][0]['field_evidence'] = {'vat_amount': {'text': '0.00', 'page': 1}}
        reconcile_with_spatial(data, spatial)
        self.assertEqual(data['items'][0]['vat_amount'], 0)
