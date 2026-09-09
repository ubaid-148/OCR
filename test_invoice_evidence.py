import copy
import io
import json
import os
import unittest
from unittest.mock import patch

from invoice_evidence import audit_ai
from local_ai_parser import _ask_ollama, _validate, parse_invoice_hybrid


def sample():
    return dict(supplier=dict(name_en="Example Supplier", vat_number="300507245400003"),
                customer=dict(name="Example Buyer", vat_number="300402905100003"),
                invoice=dict(invoice_number="INV-001", date="2026-05-18"),
                items=[dict(line_no=1, description="Steel hinges", quantity=2, unit_price=50, amount=100)],
                totals=dict(subtotal=100, discount=None, vat_rate=15, vat_amount=15, net_amount=115))


def pages():
    return [dict(page=1, words=[dict(text=t, confidence=99) for t in
            ["300507245400003", "300402905100003", "INV-001", "18/05/2026", "2", "50", "100", "15 %", "15.00", "115"]])]


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
        self.assertEqual(issues, [])
        self.assertEqual(evidence["invoice.date"]["text"], "18/05/2026")
        self.assertEqual(data["totals"]["net_amount"],115)

    def test_arithmetically_consistent_invented_amounts_are_rejected(self):
        data = sample()
        data["items"][0].update(unit_price=100, amount=200)
        data["totals"].update(subtotal=200, vat_amount=30, net_amount=230)
        self.assertFalse(_validate(data)[1]["needs_review"])
        issues,_ = audit_ai(data,pages())
        self.assertIsNone(data["totals"]["net_amount"])
        self.assertIsNone(data["items"][0]["amount"])
        self.assertTrue(issues)
        self.assertTrue(_validate(data)[1]["needs_review"])

    def test_id_substring_is_not_evidence(self):
        data=sample(); data["invoice"]["invoice_number"]="001"
        audit_ai(data,pages())
        self.assertIsNone(data["invoice"]["invoice_number"])

    def test_low_confidence_is_reported(self):
        source=pages(); source[0]["words"][-1]["confidence"]=40
        issues,_=audit_ai(sample(),source)
        self.assertTrue(any(i["field"]=="totals.net_amount" for i in issues))

    def test_ai_cannot_silently_drop_a_row(self):
        fallback_data=sample()
        fallback_data["items"].append(dict(line_no=2, quantity=1, unit_price=None, amount=None))
        checks,quality=_validate(fallback_data); fallback_data["validation"]=checks
        fallback=dict(data=fallback_data,quality=quality)
        with patch.dict(os.environ,{"USE_LOCAL_AI":"true"}), \
             patch("local_ai_parser.parse_invoice",return_value=copy.deepcopy(fallback)), \
             patch("local_ai_parser.parse_layout",return_value=None), \
             patch("local_ai_parser._ask_ollama",return_value=sample()):
            result=parse_invoice_hybrid(pages(),"test.pdf","eng")
        self.assertEqual(len(result["data"]["items"]),2)
        self.assertEqual(result["quality"]["local_ai_status"],"rejected_less_complete_result")

    def test_truncated_response_is_not_accepted(self):
        response=io.BytesIO(json.dumps(dict(done=True,done_reason="length",message=dict(content=json.dumps(sample())))).encode())
        with patch("local_ai_parser.urllib.request.urlopen",return_value=response):
            with self.assertRaisesRegex(ValueError,"truncated"):
                _ask_ollama([])


if __name__ == "__main__":
    unittest.main()
