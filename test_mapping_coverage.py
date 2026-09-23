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

    def test_footer_zero_cannot_support_blank_item_vat(self):
        from test_layout_invoice import box
        from invoice_evidence import audit_ai
        from test_invoice_evidence import sample
        words = [box(t,x,100) for t,x in [('Description',100),('Qty',400),('Rate',600),('Amount',800),('VAT',1000)]]
        words += [box(t,x,200) for t,x in [('Product',100),('2',400),('50',600),('100',800)]]
        words += [box('Discount',800,250),box('0.00',1000,250)]
        pages=[{'page':1,'words':words}]
        data=sample()
        data['items']=[dict(description='Product',quantity=2,unit_price=50,amount=100,vat_amount=0)]
        data['totals']['discount']=0
        issues,evidence=audit_ai(data,pages,enforce_items=True)
        self.assertIsNone(data['items'][0]['vat_amount'])
        self.assertEqual(evidence['items[0].amount']['text'],'100')
        self.assertNotIn('items[0].vat_amount',evidence)
        parsed={'data':data,'quality':{'field_evidence':evidence}}
        attach_mapping_coverage(parsed,pages)
        zero=next(r for r in parsed['mapping_coverage']['records'] if r['text']=='0.00')
        self.assertEqual(zero['fields'],['totals.discount'])

    def test_live_fixture_footer_does_not_verify_item_vat(self):
        import json
        from pathlib import Path
        from local_ai_parser import parse_invoice_hybrid
        from invoice_evidence import audit_ai
        payload=json.loads(Path('tests/fixtures/9480_live_ocr.json').read_text())
        data=parse_invoice_hybrid(payload['pages'],'renamed.pdf','eng+ara',mode='fast')['data']
        data['items'][0]['vat_amount']=0
        _,evidence=audit_ai(data,payload['pages'],enforce_items=True)
        self.assertIsNone(data['items'][0]['vat_amount'])
        self.assertNotIn('items[0].vat_amount',evidence)

    def test_equal_value_in_other_row_or_column_is_not_evidence(self):
        from test_layout_invoice import box
        from invoice_evidence import audit_ai
        from test_invoice_evidence import sample
        words=[box(t,x,100) for t,x in [('Description',100),('Qty',400),('Rate',600),('Amount',800),('VAT',1000)]]
        words += [box(t,x,200) for t,x in [('First',100),('2',400),('50',600),('100',800)]]
        words += [box(t,x,300) for t,x in [('Second',100),('1',400),('100',600),('100',800),('15',1000)]]
        data=sample()
        data['items']=[dict(description='First',quantity=2,unit_price=50,amount=100,vat_amount=15),
                       dict(description='Second',quantity=1,unit_price=100,amount=100,vat_amount=15)]
        _,evidence=audit_ai(data,[{'page':1,'words':words}],enforce_items=True)
        self.assertIsNone(data['items'][0]['vat_amount'])
        self.assertEqual(data['items'][1]['vat_amount'],15)
        self.assertEqual(evidence['items[1].vat_amount']['text'],'15')
        data['items'][0]['vat_amount']=50
        audit_ai(data,[{'page':1,'words':words}],enforce_items=True)
        self.assertIsNone(data['items'][0]['vat_amount'])

    def test_coverage_rejects_stale_cross_region_item_evidence(self):
        word=dict(text='0.00',left=900,top=900,width=50,height=20,confidence=99)
        proof=dict(page=1,text='0.00',bbox=[900,900,50,20])
        parsed={'data':{'items':[{'description':'Product','vat_amount':0,'field_evidence':{'vat_amount':proof}}]},
                'quality':{'field_evidence':{'items[0].vat_amount':proof,'totals.discount':proof}}}
        attach_mapping_coverage(parsed,[{'page':1,'words':[word]}])
        self.assertEqual(parsed['mapping_coverage']['records'][0]['fields'],['totals.discount'])
