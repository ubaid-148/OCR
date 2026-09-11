import unittest
from unittest.mock import patch
from ocr_web import Handler, UPLOAD_SLOT, page


class PerformanceFlowTests(unittest.TestCase):
    def test_busy_upload_does_not_enter_ocr_queue(self):
        handler = object.__new__(Handler)
        with UPLOAD_SLOT, patch.object(handler, 'send_failure') as failure, patch.object(handler, 'process_upload') as process:
            handler.do_POST()
            self.assertEqual(failure.call_args.args[1], 429)
            process.assert_not_called()

    def test_failed_upload_releases_slot(self):
        handler = object.__new__(Handler)
        with patch.object(handler, 'process_upload', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                handler.do_POST()
        self.assertTrue(UPLOAD_SLOT.acquire(blocking=False))
        UPLOAD_SLOT.release()

    def test_fast_default_and_progress_ui(self):
        body = page().decode()
        self.assertLess(body.index('value="fast"'), body.index('value="auto"'))
        self.assertIn('/app.js', body)
        self.assertIn('aria-live="polite"', body)
