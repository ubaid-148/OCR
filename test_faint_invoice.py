import unittest
from test_layout_invoice import box
from layout_invoice import parse_layout, table


def fixture():
    words=[box(t,x,300) for t,x in [('Item Code',0),('Description',200),('Qty',500),('Unit Price',650),('Total',850)]]
    words += [box(t,x,360) for t,x in [('PAINT-TIN',0),('Paint tin',200),('12.00',650),('24.00',850)]]
    return words


class FaintInvoiceTests(unittest.TestCase):
    def test_missing_quantity_keeps_alphabetic_code_row(self):
        rows,_,_=table(fixture())
        self.assertEqual(len(rows),1)
        self.assertIsNone(rows[0]['quantity'])
        self.assertEqual(rows[0]['item_code'],'PAINT-TIN')
        self.assertEqual(rows[0]['amount'],24)

    def test_excluding_and_including_vat_have_separate_roles(self):
        words=fixture()+[box(t,x,y) for t,x,y in [('Total (Excl) VAT',600,600),('24.00',850,600),('VAT 15%',600,660),('3.60',850,660),('Total With VAT',600,720),('27.60',850,720)]]
        data=parse_layout([{'words':words}],'test.pdf','eng')
        self.assertEqual(data['totals']['subtotal'],24)
        self.assertEqual(data['totals']['vat_amount'],3.6)
        self.assertEqual(data['totals']['net_amount'],27.6)

    def test_vat_and_date_are_not_invoice_identifiers(self):
        words=fixture()+[box('Invoice No',600,100),box('11/03/2026',200,100),box('300402905200003',400,100)]
        data=parse_layout([{'words':words}],'test.pdf','eng')
        self.assertIsNone(data['invoice']['invoice_number'])

    def test_customer_vat_can_precede_customer_name(self):
        words=fixture()+[box('Supplier Trading',0,0),box('300056327900003',0,30),box('الرقم الضربي للعميل',400,140),box('300402905200003',400,115),box('اسم العميل',600,200),box('Customer Trading Est.',100,200)]
        data=parse_layout([{'words':words}],'test.pdf','eng+ara')
        self.assertEqual(data['customer']['vat_number'],'300402905200003')
