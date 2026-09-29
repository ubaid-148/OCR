import unittest
from unittest.mock import Mock, patch
from ocr_web import Handler, UPLOAD_SLOT, page


class PerformanceFlowTests(unittest.TestCase):
    def test_disconnected_browser_does_not_trigger_error_response(self):
        for stage in ('headers', 'body'):
            with self.subTest(stage=stage):
                handler = object.__new__(Handler)
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock(side_effect=BrokenPipeError() if stage == 'headers' else None)
                handler.wfile = Mock()
                handler.wfile.write.side_effect = ConnectionResetError()
                handler.log_message = Mock()
                handler.process_upload = lambda: handler.send_json({'status': 'needs_review'})
                with patch.object(handler, 'send_failure') as failure:
                    handler.do_POST()
                failure.assert_not_called()
                handler.send_response.assert_called_once_with(200)
                self.assertTrue(handler.close_connection)
                self.assertTrue(UPLOAD_SLOT.acquire(blocking=False))
                UPLOAD_SLOT.release()

    def test_busy_upload_does_not_enter_ocr_queue(self):
        handler = object.__new__(Handler)
        with UPLOAD_SLOT, patch.object(handler, 'send_failure') as failure, patch.object(handler, 'process_upload') as process:
            handler.do_POST()
            self.assertEqual(failure.call_args.args[1], 429)
            process.assert_not_called()

    def test_failed_upload_releases_slot(self):
        handler = object.__new__(Handler)
        with patch.object(handler, 'process_upload', side_effect=RuntimeError('test')), \
             patch.object(handler, 'send_failure') as failure:
            handler.do_POST()
        failure.assert_called_once_with('The PDF could not be processed safely.', 500)
        self.assertTrue(UPLOAD_SLOT.acquire(blocking=False))
        UPLOAD_SLOT.release()

    def test_accuracy_default_and_progress_ui(self):
        body = page().decode()
        self.assertLess(body.index('value="auto"'), body.index('value="fast"'))
        self.assertIn('<details class="advanced">', body)
        self.assertNotIn('<label>Output', body)
        self.assertIn('/app.js', body)
        self.assertIn('aria-live="polite"', body)
