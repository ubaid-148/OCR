import unittest

from layout_invoice import table, numeric, parse_layout
from invoice_formatter import money
from local_ai_parser import _validate, _compact_ocr


def box(text, x, y, scale=1):
    return dict(text=text, left=x*scale, top=y*scale, width=80*scale,
                height=20*scale, confidence=99)


class LayoutInvoiceTests(unittest.TestCase):
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

    def test_ai_input_keeps_every_box_and_page(self):
        pages = [dict(page=n, width=600, height=800, render_dpi=200,
                      words=[box("Item B", 400, 400), box("Item A", 100, 100)]) for n in (1,2)]
        compact = _compact_ocr(pages)
        self.assertEqual([p["page"] for p in compact], [1,2])
        for page in compact:
            self.assertEqual([b[-1] for b in page["boxes"]], ["Item A", "Item B"])
            self.assertTrue(all(len(b)==5 for b in page["boxes"]))

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


if __name__ == "__main__":
    unittest.main()
