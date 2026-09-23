import unittest
from mapping_coverage import attach_mapping_coverage
from invoice_response import clean_invoice_response, validate_response_schema
from invoice_result import extract_result
from unittest.mock import patch


class MappingCoverageTests(unittest.TestCase):
    def test_same_number_on_another_page_remains_unassigned(self):
        word=dict(text='40',left=10,top=20,width=30,height=10,confidence=99)
        parsed={'data':{'field_evidence':{'totals.subtotal':dict(page=1,text='40',bbox=[10,20,30,10])}},'quality':{}}
        attach_mapping_coverage(parsed,[{'page':1,'words':[word]},{'page':2,'words':[word]}])
        self.assertEqual(parsed['unmapped_text'],[{'page':2,'text':'40'}])
        self.assertEqual(parsed['mapping_coverage']['assigned_boxes'],1)
        response=clean_invoice_response(parsed)
        self.assertEqual(response['unmapped_text'],parsed['unmapped_text'])
        validate_response_schema(response)

    def test_web_preserves_fields_already_available_in_compact_result(self):
        parsed={'data':{'supplier':{'address':'Road 1','business_type':'Trading','cr_number':'00123'},
                        'totals':{'amount_in_words':'One hundred'},'bank_details':{'account_no':'000987'}},
                'quality':{'needs_review':True},'unmapped_text':[{'page':1,'text':'Extra: 10'}]}
        response=clean_invoice_response(parsed)
        self.assertEqual(response['data']['supplier']['commercial_registration'],'00123')
        self.assertEqual(response['data']['supplier']['address'],'Road 1')
        self.assertEqual(response['data']['totals']['amount_in_words'],'One hundred')
        self.assertEqual(response['data']['bank_details']['account_no'],'000987')
        with patch('invoice_result.parse_invoice_hybrid',return_value=parsed):
            compact=extract_result({'pages':[]},'example.pdf')
        self.assertEqual(compact['unmapped_text'],response['unmapped_text'])
