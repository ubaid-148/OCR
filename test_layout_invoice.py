import unittest

from layout_invoice import table, numeric, parse_layout, table_retry_reasons
from invoice_formatter import money
from local_ai_parser import _validate


def box(text, x, y, scale=1):
    return dict(text=text, left=x*scale, top=y*scale, width=80*scale,
                height=20*scale, confidence=99)


class LayoutInvoiceTests(unittest.TestCase):
    def summary_page(self):
        words=[box(t,x,300) for t,x in [('Description',100),('Qty',400),('Rate',600),('Amount',800)]]
        words += [box(t,x,360) for t,x in [('Example item',100),('2',400),('10',600),('20',800)]]
        words += [box('VAT Summary',700,500)]
        words += [box(t,x,540) for t,x in [('Inc Tax',500),('Tax Amount',700),('Before Tax',900)]]
        words += [box(t,x,580) for t,x in [('23.00',500),('3.00',700),('20.00',900)]]
        return words

    def test_vat_summary_recovers_missing_totals_with_printed_evidence(self):
        result=parse_layout([{'words':self.summary_page()}],'arbitrary.pdf','eng')
        for key,value in [('subtotal',20),('vat_amount',3),('net_amount',23)]:
            self.assertEqual(result['totals'][key],value)
            self.assertEqual(result['field_evidence']['totals.'+key]['bbox'][1],580)
        self.assertEqual(len(result['items']),1)

    def test_summary_does_not_replace_explicit_printed_total(self):
        words=self.summary_page()+[box('Grand Total',100,680),box('24.00',400,680)]
        result=parse_layout([{'words':words}],'arbitrary.pdf','eng')
        self.assertEqual(result['totals']['net_amount'],24)

    def test_multiple_summary_bands_are_not_mistaken_for_document_totals(self):
        words=self.summary_page()+[box(t,x,610) for t,x in [('12.00',500),('2.00',700),('10.00',900)]]
        result=parse_layout([{'words':words}],'arbitrary.pdf','eng')
        for key in ('subtotal','vat_amount','net_amount'):
            self.assertIsNone(result['totals'][key])

    def test_customer_search_stops_before_unlabelled_street_value(self):
        words=self.summary_page()+[box('Customer',1000,100),box('Street',1000,145),
                                   box('Long Avenue Road',600,145)]
        result=parse_layout([{'words':words}],'arbitrary.pdf','eng')
        self.assertIsNone(result['customer']['name'])

    def test_recovered_serial_wins_even_when_label_sorts_before_crop(self):
        words=self.summary_page()+[box('Invoice Serial: 9876',800,100),
                                   dict(box('INV-456',400,104),source='targeted_ocr',
                                        retry_kind='invoice_identifier',confidence=79)]
        result=parse_layout([{'words':words}],'arbitrary.pdf','eng')
        self.assertEqual(result['invoice']['invoice_number'],'INV-456')

    def test_wrapped_description_and_partial_row_are_retained(self):
        words = [box(t,x,300) for t,x in [("Description",100),("Qty",400),("Rate",600),("Amount",800)]]
        words += [box(t,x,360) for t,x in [("Steel hinge",100),("2",400),("3",600),("6",800)]]
        words += [box("heavy duty",100,382)]
        words += [box(t,x,440) for t,x in [("Door bolt",100),("1",400),("4",800)]]
        rows,_,_ = table(words)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["description"], "Steel hinge heavy duty")
        self.assertIsNone(rows[1]["unit_price"])
        self.assertEqual(rows[1]["amount"],4)

    def test_numeric_separators(self):
        self.assertEqual(numeric("1,234.56"),1234.56)
        self.assertEqual(numeric("١٬٢٣٤٫٥٦"),1234.56)
        self.assertEqual(numeric("1,5 SET"),1.5)

    def test_column_aliases_reordering_scaling_and_optional_codes(self):
        variants = [
            (["Description", "Qty", "Unit Price", "Amount"], [100, 400, 600, 800]),
            (["Product", "Quantity", "Rate", "Taxable Value"], [400, 100, 800, 600]),
            (["وصف", "الكمية", "السعر", "القيمة الخاضعة"], [800, 600, 400, 100]),
        ]
        for labels, positions in variants:
            for scale in (.5, 1, 2):
                with self.subTest(labels=labels, scale=scale):
                    words = [box(label, x, 300, scale) for label, x in zip(labels, positions)]
                    for y, values in [(360, ["Steel hinge", "1.5 SET", "4.00", "6.00"]),
                                      (400, ["Door bolt", "2 PCS", "3.00", "6.00"])]:
                        words += [box(value, x, y, scale) for value, x in zip(values, positions)]
                    words += [box("Subtotal", 100, 500, scale), box("12.00", 800, 500, scale)]
                    rows, _, _ = table(words)
                    self.assertEqual(len(rows), 2)
                    self.assertEqual(rows[0]["quantity"], 1.5)
                    self.assertEqual(rows[0]["description"], "Steel hinge")
                    self.assertEqual(rows[1]["unit_price"], 3)
                    self.assertIsNone(rows[0]["item_code"])

    def test_alphanumeric_item_code(self):
        words = [box(t, x, 300) for t,x in [("SKU",100),("Description",300),
                 ("Qty",500),("Rate",700),("Amount",900)]]
        words += [box(t,x,360) for t,x in [("AB-02",100),("Door hinge",300),
                  ("2",500),("3",700),("6",900)]]
        rows,_,_ = table(words)
        self.assertEqual(rows[0]["item_code"], "AB-02")

    def test_spaced_codes_and_blank_line_vat_keep_pretax_totals(self):
        words = [box(t, x, 300) for t,x in [("Item Code",100),("Description",400),
                 ("Qty",700),("Unit Price",850),("VAT",1000),("Total Amount",1150)]]
        words += [box(t,x,360) for t,x in [("BATCH SAMPLE 2",100),("Example product",400),
                  ("2",700),("5.00",850),("10.00",1150)]]
        rows,_,_=table(words)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["item_code"],"BATCH SAMPLE 2")
        self.assertEqual(rows[0]["quantity"],2)
        self.assertEqual(rows[0]["amount"],10)
        self.assertIsNone(rows[0]["vat_amount"])
        self.assertIsNone(rows[0]["gross_amount"])

    def test_printed_invoice_number_beats_oversized_handwritten_note(self):
        words = [box(t, x, 300) for t,x in [("Description",300),("Qty",600),
                 ("Unit Price",800),("Amount",1000)]]
        words += [box(t,x,360) for t,x in [("Example item",300),("1",600),("5.00",800),("5.00",1000)]]
        words += [box("Inv. No.",500,100),box("321",1050,100)]
        handwritten=box("9876",720,80);handwritten['height']=60
        words.append(handwritten)
        result=parse_layout([{'words':words}],'example.pdf','eng')
        self.assertEqual(result['invoice']['invoice_number'],'321')

    def test_customer_name_beats_joined_street_text(self):
        words = [box(t, x, 300) for t,x in [('Description',300),('Qty',600),
                 ('Unit Price',800),('Amount',1000)]]
        words += [box(t,x,360) for t,x in [('Example item',300),('1',600),
                  ('5.00',800),('5.00',1000)]]
        words += [
            box('Customer / العميل',1050,100),
            box('مؤسسة علي محمد ال ريح للمقاولات العامة',550,100),
            box('الشارعالثالث عشر',700,145),
        ]
        result=parse_layout([{'words':words}],'example.pdf','eng+ara')
        self.assertEqual(result['customer']['name'],
                         'مؤسسة علي محمد ال ريح للمقاولات العامة')

    def test_joined_street_is_not_accepted_as_customer_name(self):
        words = [box(t, x, 300) for t,x in [('Description',300),('Qty',600),
                 ('Unit Price',800),('Amount',1000)]]
        words += [box(t,x,360) for t,x in [('Example item',300),('1',600),
                  ('5.00',800),('5.00',1000)]]
        words += [box('Customer / العميل',1050,100),box('الشارعالثالث عشر',700,145)]
        result=parse_layout([{'words':words}],'example.pdf','eng+ara')
        self.assertIsNone(result['customer']['name'])

    def test_address_and_vat_rate_are_not_money(self):
        self.assertIsNone(numeric("Building No.,City : 6616,Al Khobar"))
        self.assertIsNone(numeric("15 %"))
        self.assertEqual(numeric("5.22 SET"), 5.22)
        self.assertIsNone(money("Building No.,City : 6616,Al Khobar"))
        self.assertIsNone(money("15 %"))
        self.assertEqual(float(money("SAR 5.22")), 5.22)

    def test_zero_and_variable_tax_rates(self):
        for rate in (0, 5, 15, 20):
            data = dict(supplier={}, customer={}, invoice={},
                        items=[dict(quantity=2, unit_price=50, amount=100)],
                        totals=dict(subtotal=100, discount=10, vat_rate=rate,
                                    vat_amount=90*rate/100, net_amount=90+90*rate/100))
            validation, _ = _validate(data)
            self.assertTrue(validation["vat_valid"])
            self.assertTrue(validation["net_amount_valid"])
        data["totals"]["vat_rate"] = None
        validation, quality = _validate(data)
        self.assertFalse(validation["vat_valid"])
        self.assertTrue(quality["needs_review"])

    def test_targeted_cell_headers_override_scrambled_merged_headers(self):
        words=[box(t,x,300) for t,x in [
            ('Tax Amount Quantity',130),('Taxable Amount',390),('Unit Price',540),
            ('Description',850),('Item Code',1120),('Including VAT',20),
        ]]
        corrected=[('VAT Amount',130),('Taxable Amount',390),('Unit Price',540),
                   ('Quantity',650),('Unit',750),('Description',850),('Item Code',1120),
                   ('Including VAT',20)]
        for text_value,x in corrected:
            word=box(text_value,x,300);word.update(source='targeted_ocr',retry_kind='table_cells_en')
            words.append(word)
        rows=[
            [('34.02',20),('2.22',130),('14.79',390),('14.79',540),('2',650),('PCS',750),('Oil HELIX 15/40',850),('1212',1120)],
            [('58.01',20),('7.57',130),('50.44',390),('50.44',540),('1',650),('PCS',750),('Oil HELIX 4L',850),('1218',1120)],
            [('17.01',20),('2.22',130),('14.79',390),('14.79',540),('1',650),('PCS',750),('Toyota filter D4',850),('5007',1120)],
        ]
        for index,row in enumerate(rows):
            words.extend(box(text_value,x,360+index*45) for text_value,x in row)
        parsed,header,h=table(words)
        self.assertEqual([row['item_code'] for row in parsed],['1212','1218','5007'])
        self.assertEqual([row['quantity'] for row in parsed],[2,1,1])
        self.assertEqual([row['amount'] for row in parsed],[14.79,50.44,14.79])
        self.assertEqual([row['vat_amount'] for row in parsed],[2.22,7.57,2.22])
        self.assertEqual([row['gross_amount'] for row in parsed],[34.02,58.01,17.01])
        self.assertEqual(table_retry_reasons(words,parsed,header,h),[])

    def test_mixed_arabic_description_uses_rtl_reading_order(self):
        words=[box(t,x,300) for t,x in [('Item Code',1150),('Description',850),('Unit',700),('Qty',600),('Unit Price',450),('Amount',300)]]
        words += [box(t,x,360) for t,x in [('1212',1150),('زيت شل هيلكس',900),('HELIX 15/40',800),('PCS',700),('2',600),('14.79',450),('14.79',300)]]
        row=table(words)[0][0]
        self.assertEqual(row['description'],'زيت شل هيلكس HELIX 15/40')

    def test_ruled_cell_centres_beat_right_aligned_glyph_boxes(self):
        def ruled(text,left,right,y,glyph_x):
            word=box(text,glyph_x,y)
            word.update(source='targeted_ocr',retry_kind='table_cells_en',
                        grid_column=[left,right],grid_center_x=(left+right)/2)
            return word
        columns={'gross':(20,150),'vat':(150,280),'amount':(390,520),'price':(650,780),
                 'qty':(780,900),'unit':(900,1000),'desc':(1000,1350),'code':(1350,1500)}
        headers=[('Including VAT','gross'),('VAT Amount','vat'),('Taxable Amount','amount'),
                 ('Unit Price','price'),('Quantity','qty'),('Unit','unit'),
                 ('Description','desc'),('Item Code','code')]
        words=[ruled(text,*columns[key],300,columns[key][0]) for text,key in headers]
        values=[('34.02','gross'),('2.22','vat'),('14.79','amount'),('14.79','price'),
                ('2','qty'),('PCS','unit'),('Oil HELIX','desc'),('1212','code')]
        # Every glyph is left-aligned; without grid centres several narrow
        # neighbouring numeric cells overlap the wrong header tolerance.
        words += [ruled(text,*columns[key],360,columns[key][0]) for text,key in values]
        row=table(words)[0][0]
        self.assertEqual((row['item_code'],row['quantity'],row['unit_price'],row['amount'],row['vat_amount'],row['gross_amount']),
                         ('1212',2,14.79,14.79,2.22,34.02))

    def test_per_unit_printed_columns_validate_without_rewriting_source(self):
        data=dict(supplier={},customer={},invoice={},items=[
            dict(line_no=1,quantity=2,unit_price=14.79,amount=14.79,vat_amount=2.22,discount=0,gross_amount=34.02),
            dict(line_no=2,quantity=1,unit_price=50.44,amount=50.44,vat_amount=7.57,discount=0,gross_amount=58.01),
            dict(line_no=3,quantity=1,unit_price=14.79,amount=14.79,vat_amount=2.22,discount=0,gross_amount=17.01),
        ],totals=dict(subtotal=94.81,discount=0,vat_rate=15,vat_amount=14.22,net_amount=109.03))
        validation,_=_validate(data)
        self.assertTrue(validation['items_calculation_valid'])
        self.assertTrue(validation['subtotal_valid'])
        self.assertEqual(validation['item_checks'][0]['calculation_mode'],'per_unit_printed_columns')
        self.assertFalse(validation['line_vat_sum_matches'])

    def test_impossible_row_values_are_rejected_by_arithmetic(self):
        words = [box(t, x, 300) for t, x in [
            ("Description", 100), ("Qty", 500), ("Unit Price", 700),
            ("Taxable Amount", 900), ("VAT", 1100), ("Including VAT", 1300),
        ]]
        valid_row = [
            box("Oil HELIX 15/40", 100, 360),
            box("2", 500, 360),
            box("12.50", 700, 360),
            box("12.50", 900, 360),
            box("1.88", 1100, 360),
            box("26.88", 1300, 360),
        ]
        invalid_row = [
            box("Oil HELIX 15/40", 100, 420),
            box("2", 500, 420),
            box("12.50", 700, 420),
            box("99.00", 900, 420),
            box("1.88", 1100, 420),
            box("26.88", 1300, 420),
        ]
        words += valid_row + invalid_row
        parsed, _, _ = table(words)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["quantity"], 2)
        self.assertEqual(parsed[0]["amount"], 12.5)


if __name__ == "__main__":
    unittest.main()
