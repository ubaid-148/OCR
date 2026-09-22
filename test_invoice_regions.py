import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from document_regions import invoice_words
from layout_invoice import table, numeric, header_match, parse_layout
from local_ai_parser import _validate
from targeted_ocr import _table_edges, merge_retries, plan_regions, retry_regions
from test_layout_invoice import box
from test_invoice_evidence import sample


class InvoiceRegionTests(unittest.TestCase):
    def test_numeric_retry_cannot_erase_partial_per_unit_row(self):
        words=[box(t,x,300) for t,x in [
            ('Including VAT',20),('VAT Amount',130),('Taxable Amount',390),
            ('Unit Price',540),('Quantity',650),('Description',850)]]
        words += [box(t,x,360) for t,x in [
            ('23.00',20),('1.50',130),('10.00',390),('2',650),('Example oil',850)]]
        original=box('',540,360)
        for value,expected in [('999.00',None),('10.00',10.0)]:
            with self.subTest(value=value):
                page=dict(words=copy.deepcopy(words))
                retry=dict(kind='numeric_cell',original=original,
                           words=[dict(box(value,540,360),source='targeted_ocr',retry_kind='numeric_cell')])
                merge_retries(page,[retry])
                rows,_,_=table(page['words'])
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['unit_price'],expected)
                self.assertEqual(bool(page['targeted_ocr']['rejected']),expected is None)
                self.assertEqual(page['targeted_ocr']['alternatives'],[retry])

    def test_missing_serial_and_price_use_direct_recognition(self):
        from types import SimpleNamespace
        class PdfPage:
            def get_width(self): return 72
            def get_height(self): return 72
        for kind,text in [('invoice_identifier','4567890123'),('numeric_cell','18.75')]:
            with self.subTest(kind=kind):
                predictor=SimpleNamespace(predict=lambda *a,**k: [],
                    paddlex_pipeline=SimpleNamespace(text_rec_model=lambda images:
                        [{'rec_text':text,'rec_score':.96}]))
                original=box('',20,20)
                region=dict(kind=kind,bbox=[20,20,150,40],original=original,
                            language='en',dpi=200,enhance=True)
                page=dict(words=[],render_dpi=200,canonical_width=200,canonical_height=200)
                with tempfile.TemporaryDirectory() as temporary, \
                     patch('targeted_ocr.plan_regions',return_value=[region]), \
                     patch('targeted_ocr.render_upright_page',return_value=Image.new('RGB',(200,200),'white')):
                    retries=retry_regions(PdfPage(),page,lambda *args: predictor,lambda result: result,Path(temporary))
                merge_retries(page,retries)
                self.assertEqual(page['targeted_ocr']['accepted'][0]['text'],text)

    def test_retry_planning_does_not_require_item_detection(self):
        words=[box('رقم الفاتورة',100,100),box('كود الصنف',20,300),box('اسم الصنف',300,300),box('الاجمالي',1000,300)]
        plans=plan_regions(dict(words=words,width=432,height=576,render_dpi=200))
        self.assertIn('invoice_identifier',[p['kind'] for p in plans])
        self.assertIn('table_cells',[p['kind'] for p in plans])

    def test_complete_rows_do_not_retry_whole_table_for_missing_totals(self):
        words=[box(t,x,300) for t,x in [('Item Code',20),('Description',300),('Qty',650),('Unit Price',850),('Amount',1050)]]
        words += [box(t,x,360) for t,x in [('A-101',20),('Example item',300),('2',650),('10.00',850),('20.00',1050)]]
        plans=plan_regions(dict(words=words,width=500,height=700,render_dpi=200))
        self.assertNotIn('table_cells',[p['kind'] for p in plans])
        footer=next(p for p in plans if p['kind']=='footer_totals')
        self.assertEqual(footer['bbox'][0],0)
        self.assertAlmostEqual(footer['bbox'][2],500*200/72)
        self.assertGreaterEqual(footer['bbox'][1],700*200/72*.5)

    def test_missing_printed_column_triggers_header_aware_grid_retry(self):
        words=[box(t,x,300) for t,x in [('Item Code',20),('Description',300),('Qty',650),('Unit Price',850),('Amount',1050)]]
        words += [box(t,x,360) for t,x in [('A-101',20),('Example item',300),('2',650),('10.00',850)]]
        plans=plan_regions(dict(words=words,width=500,height=700,render_dpi=200))
        table_plan=next(plan for plan in plans if plan['kind']=='table_cells')
        self.assertTrue(table_plan['recover_headers'])
        self.assertTrue(any('amount' in reason for reason in table_plan['retry_reasons']))

    def test_numeric_row_without_description_triggers_table_retry(self):
        words=[box(t,x,300) for t,x in [('Description',300),('Qty',650),('Unit Price',850),('Amount',1050)]]
        words += [box(t,x,360) for t,x in [('Example item',300),('2',650),('10.00',850),('20.00',1050)]]
        words += [box(t,x,420) for t,x in [('1',650),('5.00',850),('5.00',1050)]]
        words += [box('Subtotal',300,520),box('25.00',1050,520)]
        rows,_,_=table(words)
        self.assertEqual(len(rows),1)
        plans=plan_regions(dict(words=words,width=500,height=700,render_dpi=200))
        table_plan=next(plan for plan in plans if plan['kind']=='table_cells')
        self.assertIn('numeric item row lacks a recovered description',table_plan['retry_reasons'])
        self.assertTrue(table_plan['recover_text'])

    def test_printed_vat_above_recovered_rows_triggers_text_table_retry(self):
        words=[box(t,x,300) for t,x in [
            ('Including VAT',20),('VAT Amount',130),('Taxable Amount',390),
            ('Unit Price',540),('Quantity',650),('Unit',750),
            ('Description',850),('Item Code',1120)]]
        rows=[
            [('58.01',20),('7.57',130),('50.44',390),('50.44',540),
             ('1',650),('PCS',750),('Oil HELIX 4L',850),('1218',1120)],
            [('17.01',20),('2.22',130),('14.79',390),('14.79',540),
             ('1',650),('PCS',750),('Toyota filter D4',850),('5007',1120)],
        ]
        for index,row in enumerate(rows):
            words.extend(box(text,x,360+index*45) for text,x in row)
        words += [box('Total Vat',300,550),box('14.22',20,550)]
        plans=plan_regions(dict(words=words,width=500,height=700,render_dpi=200))
        table_plan=next(plan for plan in plans if plan['kind']=='table_cells')
        self.assertIn('printed VAT exceeds recovered item rows',table_plan['retry_reasons'])
        self.assertTrue(table_plan['recover_text'])

    def test_customer_retry_includes_value_area_left_of_combined_label(self):
        words=[box(t,x,300) for t,x in [
            ('Description',300),('Qty',650),('Unit Price',850),('Amount',1050)]]
        words += [box(t,x,360) for t,x in [
            ('Example item',300),('1',650),('5.00',850),('5.00',1050)]]
        label=box('Customer / العميل',1050,100)
        words += [label,box('الشارعالثالث عشر',700,150)]
        plans=plan_regions(dict(words=words,width=500,height=700,render_dpi=200))
        name_crops=[plan for plan in plans if plan['kind']=='customer_name_ar']
        self.assertTrue(any(crop['bbox'][2]<label['left'] for crop in name_crops))

    def test_failed_retry_keeps_other_recovered_fields(self):
        class PdfPage:
            def get_width(self): return 72
            def get_height(self): return 72
        class Predictor:
            def predict(self,path,**options):
                if 'retry-0.' in path:
                    raise RuntimeError('one crop failed')
                return [[dict(box('2',20,10),polygon=[[20,10],[100,10],[100,30],[20,30]])]]
        original=box('',60,80)
        regions=[dict(kind='date',bbox=[0,0,160,40],original=box('Date',0,0),dpi=200),
                 dict(kind='numeric_cell',bbox=[40,70,160,110],original=original,dpi=200)]
        page=dict(words=[original],render_dpi=200,canonical_width=200,canonical_height=200)
        with tempfile.TemporaryDirectory() as temporary, \
             patch('targeted_ocr.plan_regions',return_value=regions), \
             patch('targeted_ocr.render_upright_page',return_value=Image.new('RGB',(200,200),'white')):
            retries=retry_regions(PdfPage(),page,lambda *args: Predictor(),lambda result: result,Path(temporary))
        self.assertIn('one crop failed',retries[0]['error'])
        self.assertEqual(retries[1]['words'][0]['text'],'2')
        merge_retries(page,retries)
        self.assertEqual(page['targeted_ocr']['accepted'][0]['text'],'2')
        self.assertIn('date:',page['targeted_ocr_error'])

    def test_failed_grid_detection_falls_back_to_full_table_crop(self):
        class PdfPage:
            def get_width(self): return 72
            def get_height(self): return 72
        class Predictor:
            def predict(self,path,**options): return []
        region=dict(kind='table_cells',bbox=[0,20,180,140],original={},
                    language='en',recover_text=True)
        page=dict(words=[],render_dpi=200,canonical_width=200,canonical_height=200)
        with tempfile.TemporaryDirectory() as temporary, \
             patch('targeted_ocr.plan_regions',return_value=[region]), \
             patch('targeted_ocr.grid_cells',side_effect=RuntimeError('grid failed')), \
             patch('targeted_ocr.render_upright_page',return_value=Image.new('RGB',(400,400),'white')):
            retries=retry_regions(PdfPage(),page,lambda *args: Predictor(),lambda result: result,Path(temporary))
        self.assertEqual(retries[0]['kind'],'table_area')
        self.assertIn('grid failed',retries[0]['error'])

    def test_faint_arabic_uses_original_crop_when_enhancement_misses(self):
        class PdfPage:
            def get_width(self): return 72
            def get_height(self): return 72
        recovered=dict(box('جالون دهان تركي',20,10),confidence=92,
                       polygon=[[20,10],[100,10],[100,30],[20,30]])
        class Predictor:
            def predict(self,path,**options):
                return [[recovered.copy()]] if path.endswith('-plain.png') else []
        region=dict(kind='description_ar',bbox=[20,20,150,60],original=box('garbled',40,30),
                    language='ar',dpi=200,enhance=True)
        page=dict(words=[],render_dpi=200,canonical_width=200,canonical_height=200)
        with tempfile.TemporaryDirectory() as temporary, \
             patch('targeted_ocr.plan_regions',return_value=[region]), \
             patch('targeted_ocr.render_upright_page',return_value=Image.new('RGB',(200,200),'white')):
            retries=retry_regions(PdfPage(),page,lambda *args: Predictor(),lambda result: result,Path(temporary))
        self.assertEqual(retries[0]['words'][0]['text'],'جالون دهان تركي')
        self.assertNotIn('error',retries[0])

    def test_low_confidence_english_description_is_reread(self):
        words=[box(t,x,300) for t,x in [('Description',300),('Qty',650),('Unit Price',850),('Amount',1050)]]
        faint=dict(box('Door hlnge',300,360),confidence=58)
        words += [faint,box('2',650,360),box('10.00',850,360),box('20.00',1050,360)]
        plans=plan_regions(dict(words=words,width=500,height=700,render_dpi=200))
        description=next(plan for plan in plans if plan['kind']=='description')
        self.assertEqual(description['language'],'en')
        corrected=dict(box('Door hinge',300,360),source='targeted_ocr',retry_kind='description')
        page=merge_retries(dict(words=words),[dict(kind='description',original=faint,words=[corrected])])
        self.assertNotIn(faint,page['words'])
        self.assertIn(corrected,page['words'])

    def test_outer_table_rules_are_recovered_without_dropping_last_column(self):
        edges=_table_edges([168,302,412,535,656,780,890,987,1073,1478,1648],0,1654)
        self.assertEqual(len(edges),12)
        self.assertLess(edges[0],168)
        self.assertEqual(edges[-1],1648)

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
        self.assertEqual(item['printed_amount'],552)
        self.assertEqual(item['printed_vat_amount'],72)
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

    def test_faint_printed_serial_and_footer_values_can_be_promoted(self):
        serial=dict(box('2690111862',300,100),confidence=76)
        total=dict(box('94.81',100,500),confidence=72)
        label=dict(box('Total Excluding VAT',300,500),confidence=72)
        page=merge_retries(dict(words=[]),[
            dict(kind='invoice_identifier',original=box('Invoice Serial',500,100),
                 words=[serial]),
            dict(kind='footer_totals',original={},words=[label,total]),
        ])
        accepted={(item['kind'],item['text']) for item in page['targeted_ocr']['accepted']}
        self.assertIn(('invoice_identifier','2690111862'),accepted)
        self.assertIn(('footer_totals','94.81'),accepted)

    def test_customer_retry_rejects_joined_street_label(self):
        street=dict(box('الشارعالثالث عشر',300,100),confidence=95)
        page=merge_retries(dict(words=[]),[
            dict(kind='customer_name_ar',original=box('Customer',700,100),words=[street])
        ])
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
