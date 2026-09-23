import os
import unittest
from unittest.mock import patch
from visual_invoice import ask_visual, _ask_with_retry, vision_attempt_summary, VisionTruncatedError


class VisionBudgetTests(unittest.TestCase):
    def test_header_starts_at_8192_items_remain_4096(self):
        diagnostics=[]
        def respond(url, body, **kwargs):
            header='items' not in body['format']['properties']
            return {'done':True,'done_reason':'stop','message':{'content':'{}' if header else '{"items":[]}'}}
        with patch.dict(os.environ,{},clear=True), patch('visual_invoice.request_json',side_effect=respond) as request:
            ask_visual('IMAGE',1,1,diagnostics=diagnostics)
        self.assertEqual([c.args[1]['options']['num_predict'] for c in request.call_args_list],[8192,4096])
        self.assertEqual([d['status'] for d in diagnostics],['completed','completed'])
        self.assertEqual(vision_attempt_summary(diagnostics)['retry_attempt_count'],0)

    def test_header_at_ceiling_does_not_repeat_truncated_call(self):
        diagnostics=[]
        with patch.dict(os.environ,{},clear=True), patch('visual_invoice.request_json',return_value={'done_reason':'length'}) as request:
            with self.assertRaises(VisionTruncatedError):
                _ask_with_retry(['IMAGE'],1,1,'header',diagnostics)
        self.assertEqual(request.call_count,1)
        self.assertEqual(vision_attempt_summary(diagnostics)['failed_attempt_count'],1)

    def test_explicit_smaller_header_budget_can_retry_and_is_accounted(self):
        diagnostics=[]
        with patch.dict(os.environ,{'OLLAMA_HEADER_NUM_PREDICT':'4096','OLLAMA_NUM_CTX':'16384'}), \
             patch('visual_invoice.request_json',side_effect=[{'done_reason':'length'},{'message':{'content':'{}'}}]), \
             patch('visual_invoice.perf_counter',side_effect=[0,100,100,220]):
            _ask_with_retry(['IMAGE'],1,1,'header',diagnostics)
        summary=vision_attempt_summary(diagnostics)
        self.assertEqual(summary['retry_attempt_count'],1)
        self.assertEqual(summary['failed_attempt_count'],1)
        self.assertEqual(summary['total_vision_seconds'],220)
        self.assertEqual(summary['wasted_retry_seconds'],100)
        self.assertEqual(summary['retry_attempt_seconds'],120)
