"""Synthetic regressions for observed Colab v13 parsing failures."""
import unittest

from invoice_result import extract_result
from layout_invoice import numeric, parse_layout
from test_layout_invoice import box


class Source9498Tests(unittest.TestCase):
    def payload(self):
        # Synthetic text reproduces the three observed OCR shapes; no customer
        # invoice payload is published with the regression.
        words=[box(t,x,300) for t,x in [('Description',100),('Qty',400),('Unit Price',600),('Amount',800)]]
        words += [box(t,x,360) for t,x in [('Example product',100),('1.00.',400),('18.75.',600),('18.75',800)]]
        words += [box('1234567890',1100,100),box('تسلسلالفاتورة',1330,100),
                  box('العميل مؤسسة المثال للمقاولات العامة',550,200)]
        return {'pages':[{'words':words}]}

    def test_observed_ocr_shapes_recover_identity_name_and_price(self):
        payload=self.payload()
        result=extract_result(payload,'arbitrary-upload.pdf')
        self.assertEqual(result['invoice_number'],'1234567890')
        self.assertEqual(result['customer']['name'],'مؤسسة المثال للمقاولات العامة')
        self.assertEqual(len(result['items']),1)
        self.assertEqual(result['items'][0]['unit_price'],18.75)
        parsed=parse_layout(payload['pages'],'arbitrary-upload.pdf','eng+ara')
        self.assertEqual(parsed['items'][0]['field_evidence']['unit_price']['text'],'18.75.')
        self.assertEqual(parsed['field_evidence']['customer.name']['confidence'],99)

    def test_identifier_is_read_from_words_not_filename_or_reference(self):
        payload=self.payload()
        for word in payload['pages'][0]['words']:
            if word['text']=='1234567890':word['text']='8765432101'
        self.assertEqual(extract_result(payload,'9498.pdf')['invoice_number'],'8765432101')

    def test_only_complete_decimal_cells_accept_trailing_punctuation(self):
        for value,expected in [('18.75.',18.75),('2.00.',2),('١٤٫٧٩.',14.79)]:
            self.assertEqual(numeric(value),expected)
        for value in ('79.','14.7.9','14..79','price 14.79.'):
            self.assertIsNone(numeric(value))
