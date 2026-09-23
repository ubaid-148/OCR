import unittest
from colab_upload_ui import upload_values


class UploadValueTests(unittest.TestCase):
    def test_widget_v7_upload_format(self):
        self.assertEqual(upload_values({'a.pdf':{'metadata':{'name':'a.pdf'},'content':b'%PDF-'}}),{'a.pdf':b'%PDF-'})

    def test_widget_v8_upload_format(self):
        self.assertEqual(upload_values(({'name':'a.pdf','content':memoryview(b'%PDF-')},)),{'a.pdf':b'%PDF-'})
