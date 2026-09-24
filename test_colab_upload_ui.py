import unittest
import types
from contextlib import redirect_stdout
import io
from unittest.mock import Mock, patch
from colab_upload_ui import upload_values, show_upload_form


class UploadValueTests(unittest.TestCase):
    def test_widget_v7_upload_format(self):
        self.assertEqual(upload_values({'a.pdf':{'metadata':{'name':'a.pdf'},'content':b'%PDF-'}}),{'a.pdf':b'%PDF-'})

    def test_widget_v8_upload_format(self):
        self.assertEqual(upload_values(({'name':'a.pdf','content':memoryview(b'%PDF-')},)),{'a.pdf':b'%PDF-'})


class NativeUploadTests(unittest.TestCase):
    def run_upload(self, uploaded, process=None):
        files = types.SimpleNamespace(upload=Mock(return_value=uploaded))
        colab = types.ModuleType('google.colab')
        colab.files = files
        display = types.ModuleType('IPython.display')
        display.clear_output = Mock()
        process = process or Mock()
        with patch.dict('sys.modules', {'google.colab': colab, 'IPython.display': display}), redirect_stdout(io.StringIO()):
            result = show_upload_form(process, mode='fast', language='eng', diagnostics=True)
        files.upload.assert_called_once_with()
        display.clear_output.assert_called_once_with(wait=True)
        self.assertIsNone(result)
        return process

    def test_native_upload_starts_processing_once_without_widget_events(self):
        uploaded = {'a.pdf': b'%PDF-1.7', 'b.PDF': b'%PDF-1.4'}
        process = self.run_upload(uploaded)
        process.assert_called_once()
        self.assertEqual(process.call_args.args[:4], (uploaded, 'fast', 'eng', True))

    def test_cancelled_upload_does_not_process(self):
        self.run_upload({}).assert_not_called()

    def test_invalid_file_does_not_discard_valid_pdf(self):
        process = self.run_upload({'bad.pdf': b'bad', 'ok.pdf': b'%PDF-1.7'})
        self.assertEqual(process.call_args.args[0], {'ok.pdf': b'%PDF-1.7'})

    def test_processing_error_is_reported_to_notebook(self):
        with self.assertRaisesRegex(RuntimeError, 'OCR failed'):
            self.run_upload({'a.pdf': b'%PDF-1.7'}, Mock(side_effect=RuntimeError('OCR failed')))
