import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from tools.batch_local_ocr import run_batch


class BatchTests(unittest.TestCase):
    def test_resume_reuses_only_unchanged_completed_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pdfs = root/'pdfs'; pdfs.mkdir()
            source = pdfs/'a.pdf'; source.write_bytes(b'first')
            extract = Mock(return_value={'pages': []})
            first = run_batch(pdfs, root/'out', extract)
            second = run_batch(pdfs, root/'out', extract)
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(first['completed'], 1)
            self.assertEqual(second['needs_review'], 1)
            source.write_bytes(b'changed')
            run_batch(pdfs, root/'out', extract)
            self.assertEqual(extract.call_count, 2)

    def test_failure_is_saved_and_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'a.pdf').write_bytes(b'pdf')
            extract = Mock(side_effect=[RuntimeError('OCR failed'), {'pages': []}])
            self.assertEqual(run_batch(root, root/'out', extract)['errors'], 1)
            self.assertEqual(run_batch(root, root/'out', extract)['errors'], 0)


if __name__ == '__main__':
    unittest.main()
