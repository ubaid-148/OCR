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
