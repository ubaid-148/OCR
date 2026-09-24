"""Execute the upload-to-result cell with mocked Colab and OCR processes."""
from contextlib import redirect_stdout, nullcontext
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
import sys
from unittest.mock import patch


class ColabUploadTests(unittest.TestCase):
    def source(self):
        notebook = json.loads(Path('colab_setup.ipynb').read_text(encoding='utf-8'))
        return ''.join(next(c for c in notebook['cells']
                            if 'Upload PDF and get invoice result' in ''.join(c['source']))['source'])

    def run_cell(self, root, fail_ocr=False, recover_runtime=False, uploads=None):
        files = types.ModuleType('google.colab.files')
        files.upload = lambda: uploads if uploads is not None else {'invoice.pdf': b'%PDF-1.7\nfixture'}
        downloads = []
        files.download = downloads.append
        colab = types.ModuleType('google.colab')
        colab.files = files
        google = types.ModuleType('google')
        google.colab = colab
        runtime = types.ModuleType('colab_runtime')
        vision = types.ModuleType('colab_vision')
        vision.prepare_vision_runtime = lambda: 'test-vision'
        preparations = []
        runtime.prepare_runtime = lambda project, require_gpu: (
            preparations.append((project, require_gpu)) or sys.executable
        )
        ui = types.ModuleType('colab_upload_ui')
        ui.UPLOAD_UI_VERSION = 'native-upload-v2'
        ui.show_upload_form = lambda process, **kwargs: process(files.upload(), kwargs['mode'], kwargs['language'], kwargs['diagnostics'], lambda *args: None, nullcontext())
        ui.show_download = lambda path, label: files.download(str(path))
        calls = []
        invoice = {'invoice_number': 'INV-7', 'items': [],
                   'validation': {'passed': False, 'warnings': ['needs_review: missing items']}}

        worker_module = types.ModuleType('colab_worker')
        class Worker:
            def __init__(self, python, project):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def run(self, pdf, raw, output, details, filename, language, mode):
                calls.append(dict(pdf=pdf, raw=raw, output=output, details=details,
                                  filename=filename, language=language, mode=mode))
                if fail_ocr:
                    raise RuntimeError('OCR failed')
                raw.write_text('{"pages": []}')
                output.write_text(json.dumps(invoice))
                details.write_text('{"quality":{"parser":"visual_ai"}}')
        worker_module.InvoiceWorker = Worker

        output = io.StringIO()
        modules = {'google': google, 'google.colab': colab, 'google.colab.files': files,'colab_vision':vision, 'colab_upload_ui':ui, 'colab_worker':worker_module}
        scope = {'PROJECT_DIR': root}
        if recover_runtime:
            modules['colab_runtime'] = runtime
            root.joinpath('colab_runtime.py').touch()
        else:
            scope['OCR_PYTHON'] = sys.executable
        with patch.dict('sys.modules', modules), patch('importlib.reload', side_effect=lambda module: module), redirect_stdout(output):
            if fail_ocr:
                with self.assertRaisesRegex(RuntimeError, 'OCR failed'):
                    exec(compile(self.source(), 'upload-cell', 'exec'), scope)
            else:
                exec(compile(self.source(), 'upload-cell', 'exec'), scope)
        return calls, downloads, output.getvalue(), invoice, preparations

    def test_upload_displays_and_downloads_only_final_invoice(self):
        with tempfile.TemporaryDirectory() as directory:
            calls, downloads, output, invoice, _ = self.run_cell(Path(directory))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(downloads), 1)
            self.assertEqual(json.loads(Path(downloads[0]).read_text()), invoice)
            self.assertIn('INV-7', output)
            self.assertIn('needs_review', output)
            self.assertNotIn('RAW OCR', output)
            self.assertFalse(Path(calls[0]['pdf']).exists())  # Temporary PDF removed.
            diagnostic=Path(downloads[0]).with_name('invoice-invoice-diagnostics.json')
            raw=json.loads(diagnostic.read_text())
            self.assertEqual(raw['pages'],[])
            self.assertEqual(raw['extraction_details']['quality']['parser'],'visual_ai')
            self.assertEqual(calls[0]['mode'], 'auto')

    def test_ocr_failure_does_not_parse_or_download_a_result(self):
        with tempfile.TemporaryDirectory() as directory:
            calls, downloads, _, _, _ = self.run_cell(Path(directory), fail_ocr=True)
            self.assertEqual(len(calls), 1)
            self.assertEqual(downloads, [])

    def test_multiple_pdfs_continue_after_invalid_file_and_avoid_name_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            uploads = {'a?.pdf': b'%PDF-1.7\nfixture', 'bad.pdf': b'invalid',
                       'a!.PDF': b'%PDF-1.7\nfixture'}
            calls, downloads, output, _, _ = self.run_cell(Path(directory), uploads=uploads)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(downloads), 2)
            self.assertNotEqual(downloads[0], downloads[1])
            self.assertTrue(all(Path(path).is_file() for path in downloads))
            self.assertIn('Processed 2/3 PDFs', output)
            self.assertIn('bad.pdf', output)

    def test_upload_cell_recovers_when_notebook_variables_were_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls, downloads, output, _, preparations = self.run_cell(
                root, recover_runtime=True
            )
            self.assertEqual(preparations, [(root, False)])
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(downloads), 1)
            self.assertIn('preparing it now', output)


if __name__ == '__main__':
    unittest.main()
