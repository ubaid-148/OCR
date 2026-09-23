import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from tools.batch_local_ocr import run_batch


class BatchTests(unittest.TestCase):
    def test_resume_reuses_only_unchanged_completed_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pdfs = root/'pdfs'; pdfs.mkdir()
            source = pdfs/'a.pdf'; source.write_bytes(b'first')
            extract = Mock(return_value={'pages': []})
            first = run_batch(pdfs, root/'out', extract, mode="fast")
            second = run_batch(pdfs, root/'out', extract, mode="fast")
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(first['completed'], 1)
            self.assertEqual(second['needs_review'], 1)
            source.write_bytes(b'changed')
            run_batch(pdfs, root/'out', extract, mode="fast")
            self.assertEqual(extract.call_count, 2)

    def test_default_batch_reads_original_pdf_with_vision_and_invalidates_mode_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'unknown-layout.pdf'
            source.write_bytes(b'pdf')
            extract = Mock(return_value={'pages': []})
            def parse(payload, filename, language, **kwargs):
                kwargs['details'].update(quality={}, data={})
                return {'status': 'needs_review'}
            with patch('tools.batch_local_ocr.extract_result', side_effect=parse) as parser:
                run_batch(root, root / 'out', extract)
                self.assertEqual(parser.call_args.kwargs['mode'], 'auto')
                self.assertEqual(parser.call_args.kwargs['pdf_path'], source)
                run_batch(root, root / 'out', extract, mode='fast')
                self.assertEqual(parser.call_count, 2)
            self.assertTrue((root / 'out' / 'unknown-layout.details.json').is_file())

    def test_cached_vision_failure_is_retried_after_service_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'a.pdf').write_bytes(b'pdf')
            extract=Mock(return_value={'pages':[]})
            statuses=iter(['failed','vision_evidence_reviewed'])
            def parse(*args, **kwargs):
                kwargs['details'].update(quality={'local_ai_status':next(statuses)},data={})
                return {'status':'needs_review'}
            with patch('tools.batch_local_ocr.extract_result',side_effect=parse):
                run_batch(root,root/'out',extract)
                run_batch(root,root/'out',extract)
                run_batch(root,root/'out',extract)
            self.assertEqual(extract.call_count,2)

    def test_uppercase_extension_and_non_object_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.PDF').write_bytes(b'pdf')
            output = root / 'out'
            output.mkdir()
            (output / 'a.json').write_text('[1]')
            extract = Mock(return_value={'pages': []})
            self.assertEqual(run_batch(root, output, extract, mode="fast")['completed'], 1)
            extract.assert_called_once()

    def test_failure_is_saved_and_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'a.pdf').write_bytes(b'pdf')
            extract = Mock(side_effect=[RuntimeError('OCR failed'), {'pages': []}])
            self.assertEqual(run_batch(root, root/'out', extract, mode="fast")['errors'], 1)
            self.assertEqual(run_batch(root, root/'out', extract, mode="fast")['errors'], 0)


if __name__ == '__main__':
    unittest.main()
