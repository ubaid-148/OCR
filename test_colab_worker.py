import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from colab_runtime import configure_environment
from colab_worker import InvoiceWorker, serve


class WorkerTests(unittest.TestCase):
    def test_worker_keeps_models_loaded_and_survives_failed_pdf(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy(Path(__file__).with_name('colab_worker.py'), root / 'colab_worker.py')
            (root / 'coordinate_ocr.py').write_text('''
import os
calls = 0
def extract_pdf(path, language, progress):
    global calls
    calls += 1
    os.write(1, b'native log output\\n')
    progress('Recognizing page')
    if path.name == 'bad.pdf':
        raise ValueError('Invalid PDF')
    return {'pages': [], 'calls': calls, 'pid': os.getpid()}
''')
            (root / 'invoice_result.py').write_text('''
def extract_result(payload, filename, language, **kwargs):
    kwargs['details'].update(parser='test')
    return dict(payload, filename=filename)
''')
            raw, output, details = (root / name for name in ('raw.json', 'out.json', 'details.json'))
            with InvoiceWorker(sys.executable, root) as worker:
                worker.run(root/'a.pdf', raw, output, details, 'a.pdf', 'eng', 'fast')
                first = json.loads(output.read_text())
                with self.assertRaisesRegex(RuntimeError, 'Invalid PDF'):
                    worker.run(root/'bad.pdf', raw, output, details, 'bad.pdf', 'eng', 'fast')
                worker.run(root/'b.pdf', raw, output, details, 'b.pdf', 'eng', 'auto')
                last = json.loads(output.read_text())
            self.assertEqual(first['pid'], last['pid'])
            self.assertEqual(last['calls'], 3)
            self.assertEqual(last['filename'], 'b.pdf')
            self.assertIsNotNone(worker.process.poll())

    def test_protocol_reports_error_then_continues(self):
        events = []
        serve(io.StringIO('bad json\n{}\n'), events.append, lambda request, emit: None)
        self.assertIn('error', events[0])
        self.assertEqual(events[1], {'done': True})

    def test_native_text_route_enabled_unless_explicitly_overridden(self):
        with patch.dict(os.environ, {}, clear=True):
            configure_environment(False)
            self.assertEqual(os.environ['OCR_FORCE_RASTER'], 'false')
        with patch.dict(os.environ, {'OCR_FORCE_RASTER': 'true'}, clear=True):
            configure_environment(True)
            self.assertEqual(os.environ['OCR_FORCE_RASTER'], 'true')
