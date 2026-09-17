import unittest

from invoice_evidence import audit_ai
from local_ai_parser import _validate


def sample():
    return dict(supplier=dict(name_en="Example Supplier", vat_number="310000000000011"),
                customer=dict(name="Example Buyer", vat_number="310000000000022"),
                invoice=dict(invoice_number="INV-001", date="2026-05-18"),
                items=[dict(line_no=1, description="Steel hinges", quantity=2, unit_price=50, amount=100,
                            vat_amount=15, gross_amount=115)],
                totals=dict(subtotal=100, discount=None, vat_rate=15, vat_amount=15, net_amount=115))


def pages():
    return [dict(page=1, words=[dict(text=t, confidence=99) for t in
            ["310000000000011", "310000000000022", "INV-001", "18/05/2026", "2", "50", "100", "15 %", "15.00", "115"]])]


class EvidenceTests(unittest.TestCase):
    def test_missing_names_and_descriptions_still_require_review(self):
        data=sample()
        data["supplier"]["name_en"]=None
        data["items"][0]["description"]=None
        _,quality=_validate(data)
        self.assertTrue(quality["needs_review"])
        self.assertIn("supplier.name",quality["missing_fields"])
        self.assertIn("items[0].description",quality["missing_fields"])

    def test_supported_values_and_date_format_conversion(self):
        data = sample()
        issues, evidence = audit_ai(data, pages())
        self.assertTrue(any(issue["field"] == "totals.net_amount" and
                            "row/column" in issue["reason"] for issue in issues))
        self.assertEqual(evidence["invoice.date"]["text"], "18/05/2026")
        self.assertEqual(data["totals"]["net_amount"],115)

    def test_arithmetically_consistent_invented_amounts_are_rejected(self):
        data = sample()
        data["items"][0].update(unit_price=100, amount=200, vat_amount=30, gross_amount=230)
        data["totals"].update(subtotal=200, vat_amount=30, net_amount=230)
        self.assertFalse(_validate(data)[1]["needs_review"])
        issues,_ = audit_ai(data,pages())
        self.assertEqual(data["totals"]["net_amount"], 230)
        self.assertEqual(data["items"][0]["amount"], 200)
        self.assertTrue(issues)
        self.assertTrue(all(issue["needs_review"] for issue in issues))

    def test_id_substring_is_not_evidence(self):
        data=sample(); data["invoice"]["invoice_number"]="001"
        issues,_=audit_ai(data,pages())
        self.assertEqual(data["invoice"]["invoice_number"], "001")
        self.assertTrue(any(issue["field"] == "invoice.invoice_number" for issue in issues))

    def test_low_confidence_is_reported(self):
        source=pages(); source[0]["words"][-1]["confidence"]=40
        issues,_=audit_ai(sample(),source)
        self.assertTrue(any(i["field"]=="totals.net_amount" for i in issues))

    def test_seller_address_cannot_be_assigned_to_customer(self):
        data=sample();data['customer']['address']='Omar Street, Building 6595'
        source=pages()
        source[0]['words'] += [
            dict(text='Building 6595',top=100,left=100,width=100,height=20,confidence=99),
            dict(text='Customer',top=200,left=100,width=100,height=20,confidence=99),
            dict(text='Building 3518',top=250,left=100,width=100,height=20,confidence=99),
            dict(text='Description',top=400,left=100,width=100,height=20,confidence=99),
        ]
        issues,_=audit_ai(data,source)
        self.assertEqual(data['customer']['address'], 'Omar Street, Building 6595')
        self.assertTrue(any(issue['field']=='customer.address' for issue in issues))

    def test_partially_supported_customer_address_is_rejected(self):
        data=sample();data['customer']['address']='Building 3518, Post Code 623'
        source=pages()
        source[0]['words'] += [
            dict(text='Customer',top=200,left=100,width=100,height=20,confidence=99),
            dict(text='Building 3518',top=250,left=100,width=100,height=20,confidence=99),
            dict(text='Post Code 34623',top=280,left=100,width=100,height=20,confidence=99),
            dict(text='Description',top=400,left=100,width=100,height=20,confidence=99),
        ]
        issues,_=audit_ai(data,source)
        self.assertEqual(data['customer']['address'], 'Building 3518, Post Code 623')
        self.assertTrue(any(issue['field']=='customer.address' for issue in issues))

    def test_ai_swapped_item_codes_fail_printed_row_order(self):
        data=sample()
        data['items']=[dict(data['items'][0],item_code='A-1'),
                       dict(data['items'][0],line_no=2,item_code='C-3'),
                       dict(data['items'][0],line_no=3,item_code='B-2')]
        source=pages()
        source[0]['words'] += [dict(text=code,top=y,left=700,width=80,height=20,confidence=99)
                               for code,y in [('A-1',300),('B-2',340),('C-3',380)]]
        issues,_=audit_ai(data,source)
        self.assertTrue(any(issue['field']=='items.row_order' for issue in issues))

if __name__ == "__main__":
    unittest.main()
