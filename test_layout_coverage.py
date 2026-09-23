"""Cached source-layout regressions; no filenames are used by production rules."""
import json
from pathlib import Path
import unittest
from invoice_result import extract_result
from layout_invoice import numeric


class LayoutCoverageTests(unittest.TestCase):
    def result(self, name):
        data=json.loads(Path(f'tests/fixtures/{name}_ocr.json').read_text())
        return extract_result(data, 'unseen-filename.pdf')

    def test_goods_description_is_not_inferred_as_product_code(self):
        result=self.result('9515')
        self.assertEqual(result['invoice_number'],'JE-INV-000505')
        self.assertEqual(result['invoice_date'],'10 Mar 2026')
        self.assertEqual(len(result['items']),1)
        row=result['items'][0]
        self.assertIsNone(row['item_code'])
        self.assertEqual(row['unit'], 'pcs')
        self.assertEqual(row['tax_rate'], 15)
        self.assertIn('BLOWER ASSAMBLE',row['description'])
        self.assertIn('HZ 50/60',row['description'])
        self.assertEqual((row['quantity'],row['unit_price'],row['amount'],row['vat_amount'],row['gross_amount']),
                         (1,2050,2050,307.5,2357.5))

    def test_rtl_item_name_and_taxable_heading_beat_tax_fragment(self):
        result=self.result('9522')
        self.assertEqual(len(result['items']),1)
        row=result['items'][0]
        self.assertEqual(row['item_code'],'1103')
        self.assertEqual(row['tax_rate'], 15)
        self.assertEqual(result['totals']['other_charges'], 0)
        self.assertEqual(result['totals']['taxable_amount'], 40)
        self.assertEqual((row['quantity'],row['unit_price'],row['amount'],row['vat_amount'],row['gross_amount']),
                         (1,40,40,6,46))

    def test_each_quantity_and_separate_amount_total_columns_survive(self):
        result=self.result('9649')
        self.assertTrue(result['items'])
        self.assertEqual(result['items'][0]['quantity'],1)
        self.assertEqual(result['items'][0]['unit_price'],40)
        self.assertEqual(result['items'][0]['amount'],40)
        self.assertEqual(result['items'][0]['gross_amount'],46)

    def test_known_quantity_units_are_not_freeform_text(self):
        for text in ('1EA','1 EA','1 pc','20 BAG','1 DRM','1 GAL'):
            with self.subTest(text=text):
                self.assertIsNotNone(numeric(text))
        for text in ('1 invoice','1 product','1 may','123ABC'):
            with self.subTest(text=text):
                self.assertIsNone(numeric(text))


if __name__=='__main__':
    unittest.main()
