import json
import unittest
from unittest.mock import Mock, patch
from visual_recovery import recover_page, merge_item_reread
from visual_invoice import ask_visual


class VisualRecoveryTests(unittest.TestCase):
    def test_localized_description_does_not_trigger_redundant_table_reread(self):
        for key in ('description_ar', 'description_en'):
            raw = {'invoice': {'invoice_number': 'A-1', 'date': '2026-03-01'},
                   'supplier': {'name_en': 'Seller'}, 'customer': {'name': 'Buyer'},
                   'totals': {'subtotal': 10, 'vat_amount': 0, 'net_amount': 10},
                   'items': [{key: 'Product', 'quantity': 1, 'unit_price': 10, 'amount': 10}]}
            ask = Mock()
            with patch('visual_recovery.recovery_images') as render:
                result, notes = recover_page('source.pdf', 1, 1, raw, {}, ask, [])
            ask.assert_not_called()
            render.assert_not_called()
            self.assertEqual(result, raw)
            self.assertEqual(notes, [])

    def test_truncated_table_gets_one_larger_budget_retry(self):
        replies=[{'message':{'content':'{}'}},
                 {'done_reason':'length','eval_count':4096,'message':{'content':'{"items":['}},
                 {'message':{'content':'{"items":[{"description":"Part","quantity":1}]}'}}]
        diagnostics=[]
        with patch('visual_invoice.request_json',side_effect=replies) as ask:
            result=ask_visual('IMAGE',1,1,diagnostics=diagnostics)
        self.assertEqual(result['items'][0]['description'],'Part')
        self.assertEqual(ask.call_count,3)
        self.assertEqual(ask.call_args.args[1]['options']['num_predict'],8192)
        self.assertEqual([d['status'] for d in diagnostics],['completed','failed','completed'])

    def test_reread_fills_header_without_overwriting_conflicting_printed_value(self):
        raw={'invoice':{'invoice_number':None,'date':'2026-03-01'},'items':[]}
        ask=Mock(return_value={'invoice':{'invoice_number':'00042','date':'2026-04-01'}})
        with patch('visual_recovery.recovery_images',return_value=['FULL','TOP','BOTTOM']):
            result,notes=recover_page('source.pdf',1,1,raw,{},ask,[])
        self.assertEqual(result['invoice']['invoice_number'],'00042')
        self.assertEqual(result['invoice']['date'],'2026-03-01')
        self.assertTrue(any('disagree' in n for n in notes))
        self.assertIsNone(raw['invoice']['invoice_number'])
        self.assertEqual(ask.call_args.kwargs['instruction'].count('SAME page'),1)

    def test_item_reread_matches_unique_codes_and_preserves_zero(self):
        old=[{'item_code':'A','quantity':None,'discount':0}]
        new=[{'item_code':'A','quantity':2,'discount':5},{'item_code':'B','quantity':1}]
        merged,notes=merge_item_reread(old,new)
        self.assertEqual([r['quantity'] for r in merged],[2,1])
        self.assertEqual(merged[0]['discount'],0)
        self.assertTrue(notes)

    def test_reordered_or_duplicate_rows_are_not_merged_by_position(self):
        old=[{'item_code':'A','quantity':None},{'item_code':'B','quantity':1}]
        for new in ([{'item_code':'B'},{'item_code':'A'}], [{'item_code':'A'},{'item_code':'A'}]):
            merged,notes=merge_item_reread(old,new)
            self.assertEqual(merged,old)
            self.assertTrue(notes)

    def test_failed_reread_preserves_initial_extraction(self):
        raw={'invoice':{},'items':[]}
        with patch('visual_recovery.recovery_images',return_value=['FULL']), \
             patch('visual_recovery.table',return_value=([],None,20)):
            result,notes=recover_page('source.pdf',1,1,raw,{},Mock(side_effect=TimeoutError('timeout')),[])
        self.assertEqual(result,raw)
        self.assertTrue(any('retained' in note for note in notes))
