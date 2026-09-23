import unittest
from copy import deepcopy
from invoice_details import add_printed_details
from local_ai_parser import _validate
from layout_invoice import table
from test_layout_invoice import box
from test_invoice_evidence import sample


class MissingFieldTests(unittest.TestCase):
    def test_different_rates_and_wrapped_units_stay_on_their_rows(self):
        words = [box(t,x,100) for t,x in [('Description',100),('Qty',400),('Rate',600),('Amount',800),('VAT Amount',1000)]]
        for y, qty, unit, rate in [(200,'1','pcs','5%'), (280,'2','KG','20%')]:
            words += [box(t,x,y) for t,x in [('Product',100),(qty,400),('10',600),('10' if qty=='1' else '20',800),('0.5' if qty=='1' else '4',1000)]]
            words += [box(unit,400,y+25),box(rate,1000,y+25)]
        rows,_,_=table(words)
        self.assertEqual([(r['unit'],r['tax_rate']) for r in rows],[('pcs',5),('KG',20)])
        self.assertTrue(all('tax_rate' in r['field_evidence'] for r in rows))

    def test_attached_unit_is_not_lost(self):
        words=[box(t,x,100) for t,x in [('Description',100),('Qty',400),('Rate',600),('Amount',800)]]
        words += [box(t,x,200) for t,x in [('Paint',100),('1 DRM',400),('10',600),('10',800)]]
        rows,_,_=table(words)
        self.assertEqual(rows[0]['unit'],'DRM')
        self.assertEqual(rows[0]['quantity'],1)

    def test_nonzero_other_charges_are_included_in_grand_total(self):
        data=sample()
        data['totals'].update(other_charges=5,taxable_amount=100,net_amount=120)
        checks, quality=_validate(data)
        self.assertTrue(checks['net_amount_valid'])
        self.assertTrue(checks['taxable_amount_valid'])
        self.assertFalse(quality['needs_review'])

    def test_inconsistent_printed_taxable_amount_requires_review(self):
        data=sample()
        data['totals'].update(taxable_amount=90,vat_amount=13.5,net_amount=103.5)
        checks, quality=_validate(data)
        self.assertFalse(checks['taxable_amount_valid'])
        self.assertTrue(quality['needs_review'])

    def test_unfamiliar_label_keeps_printed_value_and_page(self):
        data=deepcopy(sample())
        pages=[{'page':2,'words':[box('Dispatch Reference: 0007-A',100,100),box('https://example.test',100,150)]}]
        add_printed_details(data,pages)
        add_printed_details(data,pages)
        self.assertEqual(data['other_fields'],[{'label':'Dispatch Reference','value':'0007-A','page':2}])

    def test_mixed_tax_rates_validate_against_printed_lines(self):
        data=sample()
        data['items']=[dict(description='A',quantity=1,unit_price=100,amount=100,vat_amount=5,tax_rate=5,gross_amount=105),
                       dict(description='B',quantity=1,unit_price=100,amount=100,vat_amount=20,tax_rate=20,gross_amount=120)]
        data['totals'].update(subtotal=200,vat_amount=25,net_amount=225,vat_rate=None)
        checks, quality=_validate(data)
        self.assertTrue(checks['vat_valid'])
        self.assertFalse(quality['needs_review'])
        data['items'][1]['tax_rate']=15
        self.assertFalse(_validate(data)[0]['items_calculation_valid'])

    def test_partial_table_without_price_header_keeps_printed_rows(self):
        words=[box(t,x,100) for t,x in [('Description',100),('Qty',400),('Amount',800)]]
        words += [box(t,x,200) for t,x in [('Service',100),('2',400),('20',800)]]
        rows,_,_=table(words)
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['quantity'],rows[0]['amount']),(2,20))
        self.assertIsNone(rows[0]['unit_price'])

    def test_partial_table_without_amount_header_does_not_invent_amount(self):
        words=[box(t,x,100) for t,x in [('Description',100),('Qty',400),('Rate',600)]]
        words += [box(t,x,200) for t,x in [('Service',100),('2',400),('10',600)]]
        rows,_,_=table(words)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['unit_price'],10)
        self.assertIsNone(rows[0]['amount'])

    def test_party_vat_number_is_not_a_table_tax_heading(self):
        words=[box('VAT Number',0,40)]
        words += [box(t,x,100) for t,x in [('Description',100),('Qty',400),('Rate',600)]]
        words += [box(t,x,200) for t,x in [('Service',100),('1',400),('10',600),('9',0)]]
        rows,_,_=table(words)
        self.assertIsNone(rows[0]['vat_amount'])

    def test_wide_grid_metadata_does_not_shift_tax_into_gross_column(self):
        words=[box(t,x,100) for t,x in [('Description',100),('Qty',400),('Rate',600),('Amount',800)]]
        words += [dict(box('Tax.A',1000,100),source='targeted_ocr',retry_kind='table_cells_en',
                       grid_column=[980,1380],grid_center_x=1180)]
        words += [box(t,x,200) for t,x in [('Service',100),('2',400),('10',600),('20',800),('3',1000),('23',1200)]]
        rows,_,_=table(words)
        self.assertEqual(rows[0]['vat_amount'],3)

    def test_bilingual_total_ends_partial_table_before_amount_in_words(self):
        words=[box(t,x,100) for t,x in [('Description',100),('Qty',400),('Rate',600)]]
        words += [box(t,x,200) for t,x in [('Service',100),('2',400),('10',600)]]
        words += [box('Total/الاجمالي',400,300),box('20',600,300),box('Twenty Riyals',100,340),box('20',600,340)]
        rows,_,_=table(words)
        self.assertEqual(len(rows),1)
