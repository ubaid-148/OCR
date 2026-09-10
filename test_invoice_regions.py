import copy
import unittest
from document_regions import invoice_words
from layout_invoice import table, numeric, header_match, parse_layout
from local_ai_parser import _validate
from targeted_ocr import merge_retries, plan_regions
from test_layout_invoice import box
from test_invoice_evidence import sample


class InvoiceRegionTests(unittest.TestCase):
    def test_retry_planning_does_not_require_item_detection(self):
        words=[box('رقم الفاتورة',100,100),box('كود الصنف',20,300),box('اسم الصنف',300,300),box('الاجمالي',1000,300)]
        plans=plan_regions(dict(words=words,width=432,height=576,render_dpi=200))
        self.assertIn('invoice_identifier',[p['kind'] for p in plans])
        self.assertIn('table_cells',[p['kind'] for p in plans])

    def test_joined_arabic_unit_and_price_headers(self):
        self.assertTrue(header_match('سعر افراديالوحدة',('سعر افرادي',)))

    def test_short_description_words_are_not_discarded(self):
        w=dict(box('HINGE',100,100),source='targeted_ocr',retry_kind='description')
        result=merge_retries(dict(words=[]),[dict(kind='description',original={},words=[w])])
        self.assertEqual(result['words'][0]['text'],'HINGE')

    def test_header_fields_survive_unreadable_table(self):
        words=[box('Example Trading Est.',20,20),box('اسم العميل',1000,100),box('Example Buyer Est.',300,100),
               box('كود الصنف',20,300),box('اسم الصنف',300,300),box('الاجمالي',1000,300),
               box('المجموع',800,600),box('17.39',1000,600),box('المجموع الضريبة',800,700),box('2.61',1000,700)]
        result=parse_layout([dict(words=words)],'unknown.pdf','ara')
        self.assertEqual(result['items'],[])
        self.assertEqual(result['customer']['name'],'Example Buyer Est.')
        self.assertEqual(result['totals']['subtotal'],17.39)

    def test_serial_is_not_product_code_in_any_box_order(self):
        words=[box(t,x,300) for t,x in [('S.No.',20),('Item Code',120),('Description',400),('Qty',700),('Unit Price',900),('Amount',1100)]]
        words += [box(t,x,360) for t,x in [('1',20),('AB-001',120),('Door hinge',320),('2',700),('3',900),('6',1100)]]
        for variant in (words,list(reversed(words))):
            result=table(variant)[0]
            self.assertEqual(result[0]['item_code'],'AB-001')
            self.assertEqual(result[0]['description'],'Door hinge')

    def test_gross_column_is_preserved_and_net_derivation_is_explicit(self):
        words=[box(t,x,300) for t,x in [('Item No.',20),('Description',300),('Qty',600),('Unit Price',800),('VAT 15%',1000),('Total Amount',1200)]]
        words += [box(t,x,360) for t,x in [('10000234',20),('Metal protector',300),('12',600),('40.00',800),('72.00',1000),('552.00',1200)]]
        item=table(words)[0][0]
        self.assertEqual(item['item_code'],'10000234')
        self.assertEqual(item['amount'],480)
        self.assertEqual(item['gross_amount'],552)
        self.assertEqual(item['vat_amount'],72)
        self.assertEqual(item['amount_source'],'derived_quantity_price')
        self.assertNotIn('amount',item['field_evidence'])

    def test_corrupt_numeric_suffix_is_not_silently_a_unit(self):
        self.assertIsNone(numeric('40.0b'))
        self.assertEqual(numeric('40.00 PCS'),40)

    def test_purchase_order_does_not_expand_receipt_across_invoice(self):
        words=[box('mada',50,200),box('123456****1234',50,240),box('PURCHASE',50,280),
               box('طلب شراء',900,350),box('Receipt Merchant',50,100),box('Invoice Seller',900,100)]
        clean,region=invoice_words(dict(words=words,width=432,height=576,render_dpi=200))
        self.assertIsNotNone(region)
        self.assertNotIn('Receipt Merchant',[w['text'] for w in clean])
        self.assertIn('Invoice Seller',[w['text'] for w in clean])

    def test_weak_retry_cannot_replace_amount(self):
        original=box('40.0b',100,100)
        corrected=dict(box('40.00',100,100),confidence=30,retry_kind='numeric_cell')
        page=merge_retries(dict(words=[original]),[dict(kind='numeric_cell',original=original,words=[corrected])])
        self.assertEqual(page['words'],[original])
        self.assertEqual(page['targeted_ocr']['accepted'],[])

    def test_identifier_retry_keeps_raw_text_and_exact_id(self):
        original=box('Invoice No unreadable',100,100)
        recovered=dict(box('label: INV-099',100,100),source='targeted_ocr',retry_kind='invoice_identifier')
        page=merge_retries(dict(words=[original]),[dict(kind='invoice_identifier',original=original,words=[recovered])])
        self.assertEqual(page['words'][-1]['text'],'INV-099')
        self.assertEqual(page['words'][-1]['raw_text'],'label: INV-099')

    def test_printed_tax_rounding_discrepancy_requires_review(self):
        data=sample()
        data['items'][0]['vat_amount']=15.01
        checks,quality=_validate(data)
        self.assertFalse(checks['line_vat_sum_matches'])
        self.assertTrue(quality['needs_review'])
        self.assertEqual(data['items'][0]['vat_amount'],15.01)


if __name__=='__main__':
    unittest.main()
