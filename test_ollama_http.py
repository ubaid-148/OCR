import io
import json
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import patch

from ollama_http import preload, request_json


class OllamaHttpTests(unittest.TestCase):
    def test_http_500_keeps_server_reason(self):
        error = urllib.error.HTTPError("http://localhost/api/chat", 500, "Internal Server Error", {},
                                       io.BytesIO(b'{"error":"model runner failed to load"}'))
        with patch("ollama_http.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "Ollama HTTP 500: model runner failed to load"):
                request_json("http://localhost/api/chat", {}, 1)

    def test_preload_failure_is_explicit_and_nonfatal(self):
        output = io.StringIO()
        with patch("ollama_http.request_json", side_effect=RuntimeError("Ollama HTTP 500: test failure")), redirect_stdout(output):
            self.assertFalse(preload("test-model"))
        self.assertIn("test failure", output.getvalue())
        self.assertIn("AI remains enabled", output.getvalue())

    def test_preload_requires_completed_response(self):
        with patch("ollama_http.request_json", return_value={"done":False}), redirect_stdout(io.StringIO()):
            self.assertFalse(preload("test-model"))
        with patch("ollama_http.request_json", return_value={"done":True}), redirect_stdout(io.StringIO()):
            self.assertTrue(preload("test-model"))


if __name__ == "__main__":
    unittest.main()
