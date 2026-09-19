"""Mocked general extraction integration; no models are run locally."""
import os
import unittest
from unittest.mock import patch

from invoice_result import extract_result
from colab_vision import prepare_vision_runtime


class GeneralExtractionTests(unittest.TestCase):
    def test_auto_uses_original_pdf_and_retains_review_and_diagnostics(self):
        parsed={'data':{'invoice':{'invoice_number':'INV-EXAMPLE'},'items':[],
                        'validation':{}},
                'quality':{'parser':'visual_ai','needs_review':True,
                           'review_reasons':['Unverified customer.name']}}
        details={}
        with patch.dict(os.environ,{'USE_LOCAL_AI':'true'}), \
             patch('visual_invoice.parse_invoice_visual',return_value=parsed) as vision, \
             patch('invoice_result.parse_invoice_hybrid') as spatial:
            result=extract_result({'pages':[]},'any-layout.pdf',pdf_path='original.pdf',
                                  mode='auto',details=details)
        vision.assert_called_once_with('original.pdf',[],'any-layout.pdf','eng+ara',mode='auto')
        spatial.assert_not_called()
        self.assertEqual(result['invoice_number'],'INV-EXAMPLE')
        self.assertEqual(result['status'],'needs_review')
        self.assertIn('Unverified customer.name',result['review_notes'])
        self.assertEqual(details,parsed)

    def test_auto_does_not_silently_skip_disabled_vision(self):
        with patch.dict(os.environ,{'USE_LOCAL_AI':'false'}):
            with self.assertRaisesRegex(RuntimeError,'vision setup'):
                extract_result({},'invoice.pdf',mode='auto',pdf_path='original.pdf')
        with self.assertRaisesRegex(ValueError,'original PDF'):
            extract_result({},'invoice.pdf',mode='auto')

    def test_ready_vision_runtime_reuses_model_without_install(self):
        with patch.dict(os.environ,{},clear=True), \
             patch('colab_vision.Path.is_dir',return_value=True), \
             patch('colab_vision.shutil.which',return_value='/usr/bin/ollama'), \
             patch('colab_vision._tags',return_value={'models':[{'name':'qwen3-vl:4b'}]}), \
             patch('ollama_http.request_json',return_value={'capabilities':['vision']}), \
             patch('colab_vision.subprocess.run') as run:
            self.assertEqual(prepare_vision_runtime(),'qwen3-vl:4b')
            self.assertEqual(os.environ['USE_LOCAL_AI'],'true')
            run.assert_not_called()

    def test_text_only_model_does_not_enable_general_mode(self):
        with patch.dict(os.environ,{},clear=True), \
             patch('colab_vision.Path.is_dir',return_value=True), \
             patch('colab_vision.shutil.which',return_value='/usr/bin/ollama'), \
             patch('colab_vision._tags',return_value={'models':[{'name':'text-only'}]}), \
             patch('ollama_http.request_json',return_value={'capabilities':['completion']}):
            with self.assertRaisesRegex(RuntimeError,'does not support'):
                prepare_vision_runtime('text-only')
            self.assertEqual(os.environ['USE_LOCAL_AI'],'false')
