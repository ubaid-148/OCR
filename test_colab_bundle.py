from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile
from tools.build_colab_bundle import build, LOADER


class ColabBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temporary.name)
        cls.result = build(cls.output)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def execute_loader(self, archive_bytes, target):
        files = types.SimpleNamespace(upload=lambda: {'invoice-ocr-code.zip':archive_bytes})
        colab = types.ModuleType('google.colab'); colab.files = files
        scope = {}
        source = LOADER.replace("pathlib.Path('/content')", f'pathlib.Path({str(target)!r})')
        with patch.dict('sys.modules',{'google.colab':colab}), patch('os.chdir'), redirect_stdout(io.StringIO()):
            exec(compile(source,'bundle-loader','exec'),scope)
        return scope

    def test_generated_notebook_loads_exact_bundle_with_manifest(self):
        archive_path = Path(self.result['archive'])
        scope = self.execute_loader(archive_path.read_bytes(),self.output/'runtime')
        project = scope['PROJECT_DIR']
        manifest = json.loads((project/'BUNDLE_MANIFEST.json').read_text())
        for name,digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((project/name).read_bytes()).hexdigest(),digest)
        self.assertTrue((project/'visual_recovery.py').is_file())
        self.assertTrue((project/'tests/fixtures/9480_recovered_ocr.json').is_file())
        notebook=json.loads(Path(self.result['notebook']).read_text())
        self.assertIn("globals().get('PROJECT_DIR'",''.join(notebook['cells'][2]['source']))
        self.assertIn('uploaded_code = files.upload()', ''.join(notebook['cells'][1]['source']))

    def test_loader_rejects_traversal_before_extracting(self):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr('OCR/../../outside.py','bad')
        with self.assertRaisesRegex(ValueError,'Invalid code archive path'):
            self.execute_loader(buffer.getvalue(),self.output/'runtime2')
        self.assertFalse((self.output/'outside.py').exists())
