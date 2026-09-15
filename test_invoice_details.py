import unittest
from test_layout_invoice import box
from layout_invoice import parse_layout, table
from targeted_ocr import merge_retries
from invoice_details import add_printed_details


class InvoiceDetailsTests(unittest.TestCase):
    def rows(self):
        words=[box(t,x,300) for t,x in [('ITEM',0),('Description',200),('Qty',500),('Unit Price',650),('Amount',850),('VAT 15%',1000)]]
        for i,(code,qty) in enumerate([('AA-1','blur'),('AA-2','1'),('AA-3','blur')]):
            words.extend(box(t,x,360+i*60) for t,x in [(code,0),('Printed product',200),(qty,500),('10',650),('10',850),('1.50',1000)])
        return words

    def test_missing_quantities_do_not_remove_rows(self):
        rows,_,_=table(self.rows())
        self.assertEqual([r['item_code'] for r in rows],['AA-1','AA-2','AA-3'])
        self.assertEqual([r['quantity'] for r in rows],[None,1,None])

    def test_same_row_serial_does_not_suppress_recovered_quantity(self):
        original=box('blur',500,360)
        retry=dict(box('1',500,360),source='targeted_ocr')
        page=merge_retries({'words':[box('1',0,360),original]},[dict(kind='numeric_cell',original=original,words=[retry])])
        self.assertIn(retry,page['words'])

    def test_shared_total_row_and_supplier_vat_below_customer(self):
        words=self.rows()+[box(t,x,y) for t,x,y in [('Supplier Trading',0,0),('CUSTOMER DETAILS :',0,100),('تفاصيل العملاء',300,100),('M/S. Example Est',0,130),('CUSTOMER VAT : 300000000000003',0,180),('VAT No : 310000000000003',700,200),('INVOICE NO',700,100),(': 4567',850,100),('TOTAL',650,600),('30.00',850,600),('4.50',1000,600),('TOTAL INCLUDING VAT',650,660),('34.50',1000,660)]]
        data=parse_layout([dict(words=words)],'example.pdf','eng+ara')
        self.assertEqual(len(data['items']),3)
        self.assertEqual(data['invoice']['invoice_number'],'4567')
        self.assertEqual(data['supplier']['vat_number'],'310000000000003')
        self.assertEqual(data['customer']['name'],'M/S. Example Est')
        self.assertEqual(data['totals']['subtotal'],30)
        self.assertEqual(data['totals']['vat_amount'],4.5)
        self.assertEqual(data['totals']['net_amount'],34.5)

    def test_printed_bank_details_preserve_identifier_strings(self):
        data={'supplier':{},'customer':{},'totals':{}}
        words=[box(t,x,y) for t,x,y in [('BANK DETAILS :',0,100),('Bank',0,140),(': EXAMPLE BANK',120,140),('A/c No.',0,180),(': 001234567890',120,180),('IBAN',0,220),(': SA001234567890',120,220),('AUTHORIZED SIGNATURE:',0,300)]]
        add_printed_details(data,[dict(words=words)])
        self.assertEqual(data['bank_details']['account_no'],'001234567890')
        self.assertNotIn('notes',data)

    def test_arabic_customer_invoice_labels_and_three_rows(self):
        headers=[('رقم الصنف',1200),('اسم الصنف',950),('الوحدة',760),('الكمية',650),
                 ('سعر الوحدة',520),('الخصم',420),('المبلغ الخاضع للضريبة',300),
                 ('نسبة الضريبة',220),('مبلغ الضريبة',130),('الإجمالي شامل الضريبة',20)]
        words=[box(text,x,300) for text,x in headers]
        rows=[
            [('A-1012',1200),('منتج تجريبي أول',900),('PCS',760),('2',650),('10.00',520),('0.00',420),('10.00',300),('15%',220),('1.50',130),('23.00',20)],
            [('A-1018',1200),('منتج تجريبي ثان',900),('PCS',760),('1',650),('20.00',520),('0.00',420),('20.00',300),('15%',220),('3.00',130),('23.00',20)],
            [('A-5001',1200),('منتج تجريبي ثالث',900),('PCS',760),('1',650),('10.00',520),('0.00',420),('10.00',300),('15%',220),('1.50',130),('11.50',20)],
        ]
        for index,row in enumerate(rows):
            words.extend(box(text,x,360+index*55) for text,x in row)
        words += [
            box('شركة المثال التجارية',900,20),
            box('الرقم الضريبي للمورد: 310000000000003',850,70),
            box('مسلسل الفاتورة: 1234567890',850,110),
            box('تاريخ إصدار الفاتورة: 01/01/2026 10:20:30',750,145),
            box('كود العميل: 123456',100,190),
            box('مؤسسة المثال للمقاولات العامة',350,190),
            box('الرقم الضريبي للعميل: 300000000000003',300,230),
            box('طريقة الدفع: Span Card - Mada',800,260),
            box('الإجمالي قبل الضريبة',700,570),box('50.00',1000,570),
            box('إجمالي ضريبة القيمة المضافة',700,610),box('7.50',1000,610),
            box('إجمالي المبلغ شامل الضريبة',700,650),box('57.50',1000,650),
        ]
        data=parse_layout([dict(words=words)],'example-ar.pdf','eng+ara')
        self.assertEqual(data['invoice']['invoice_number'],'1234567890')
        self.assertEqual(data['invoice']['date'],'01/01/2026')
        self.assertEqual(data['invoice']['time'],'10:20:30')
        self.assertEqual(data['invoice']['payment_method'],'Span Card - Mada')
        self.assertEqual(data['customer']['name'],'مؤسسة المثال للمقاولات العامة')
        self.assertEqual(data['customer']['vat_number'],'300000000000003')
        self.assertEqual([item['item_code'] for item in data['items']],['A-1012','A-1018','A-5001'])
        self.assertEqual([item['quantity'] for item in data['items']],[2,1,1])
        self.assertEqual(data['items'][0]['amount'],10)
        self.assertEqual(data['totals']['subtotal'],50)
        self.assertEqual(data['totals']['vat_amount'],7.5)
        self.assertEqual(data['totals']['net_amount'],57.5)

    def test_supply_date_and_address_stay_inside_customer_section(self):
        words=[box(t,x,300) for t,x in [('Description',300),('Qty',600),('Unit Price',800),('Amount',1000)]]
        words += [box(t,x,360) for t,x in [('Example item',300),('1',600),('5.00',800),('5.00',1000)]]
        words += [box(t,x,y) for t,x,y in [
            ('Invoice Date: 09/03/2026 11:19:38',700,60),
            ('Date of Supply: 02-04-2026',700,100),
            ('Seller Building: 6595',100,120),
            ('Customer',700,160),('مؤسسة المثال للمقاولات العامة',350,160),
            ('3518 : Building / المبنى',100,200),('الثالث عشر : Street / الشارع',420,200),
            ('34623 : Post Code / الرمز البريدي',100,230),('الثقبة : Area / الحي',420,230),
            ('8096 : Add No / الرقم الإضافي',100,260),('الخبر : City / المدينة',420,260),
        ]]
        data=parse_layout([dict(words=words)],'address.pdf','eng+ara')
        self.assertEqual(data['invoice']['date_of_supply'],'02-04-2026')
        self.assertIn('3518',data['customer']['address'])
        self.assertIn('الثالث عشر',data['customer']['address'])
        self.assertNotIn('6595',data['customer']['address'])
