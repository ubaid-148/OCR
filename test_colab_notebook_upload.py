"""Execute the upload-to-result cell with mocked Colab and OCR processes."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch


class ColabUploadTests(unittest.TestCase):
    def source(self):
        notebook = json.loads(Path('colab_setup.ipynb').read_text(encoding='utf-8'))
        return ''.join(next(c for c in notebook['cells']
                            if 'Upload PDF and get invoice result' in ''.join(c['source']))['source'])

    def run_cell(self, root, fail_ocr=False):
        files = types.ModuleType('google.colab.files')
        files.upload = lambda: {'invoice.pdf': b'%PDF-1.7\nfixture'}
        downloads = []
        files.download = downloads.append
        colab = types.ModuleType('google.colab')
        colab.files = files
        google = types.ModuleType('google')
        google.colab = colab
        calls = []
        invoice = {'invoice_number': 'INV-7', 'items': [],
                   'validation': {'passed': False, 'warnings': ['needs_review: missing items']}}

        def process(command, **kwargs):
            calls.append(command)
            if 'coordinate_ocr.py' in command[2]:
                if fail_ocr:
                    return types.SimpleNamespace(returncode=1, stderr='OCR failed', stdout='')
                Path(command[4]).write_text('{"pages": []}')
            else:
                Path(command[command.index('--output') + 1]).write_text(json.dumps(invoice))
            return types.SimpleNamespace(returncode=0, stderr='', stdout='')

        output = io.StringIO()
        with patch.dict('sys.modules', {'google': google, 'google.colab': colab,
                                       'google.colab.files': files}), patch('subprocess.run', side_effect=process), redirect_stdout(output):
            if fail_ocr:
                with self.assertRaisesRegex(RuntimeError, 'OCR failed'):
                    exec(compile(self.source(), 'upload-cell', 'exec'), {'PROJECT_DIR': root, 'OCR_PYTHON': 'python'})
            else:
                exec(compile(self.source(), 'upload-cell', 'exec'), {'PROJECT_DIR': root, 'OCR_PYTHON': 'python'})
        return calls, downloads, output.getvalue(), invoice

    def test_upload_displays_and_downloads_only_final_invoice(self):
        with tempfile.TemporaryDirectory() as directory:
            calls, downloads, output, invoice = self.run_cell(Path(directory))
            self.assertEqual(len(calls), 2)
            self.assertIn('--no-llm', calls[1])
            self.assertEqual(len(downloads), 1)
            self.assertEqual(json.loads(Path(downloads[0]).read_text()), invoice)
            self.assertIn('INV-7', output)
            self.assertIn('needs_review', output)
            self.assertNotIn('RAW OCR', output)
            self.assertFalse(Path(calls[0][3]).exists())  # Temporary PDF removed.

    def test_ocr_failure_does_not_parse_or_download_a_result(self):
        with tempfile.TemporaryDirectory() as directory:
            calls, downloads, _, _ = self.run_cell(Path(directory), fail_ocr=True)
            self.assertEqual(len(calls), 1)
            self.assertEqual(downloads, [])


if __name__ == '__main__':
    unittest.main()
