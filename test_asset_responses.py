"""Regression checks for manifest and service-worker HTTP responses."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class AssetResponseTests(unittest.TestCase):
    def request_asset(self, root, path):
        handler = app.Handler.__new__(app.Handler)
        handler.path = path
        handler.command = "GET"
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"GET {path} HTTP/1.1"
        handler.wfile = io.BytesIO()
        with patch.object(app, "ROOT", root), patch.object(
            handler, "handle_staffbase_get", return_value=False
        ), patch.object(handler, "log_request"), patch.object(handler, "log_error"):
            handler.do_GET()
        return handler.wfile.getvalue()

    def test_missing_assets_send_one_404_response(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in ("/manifest.json", "/sw.js"):
                with self.subTest(path=path):
                    response = self.request_asset(Path(directory), path)
                    self.assertTrue(response.startswith(b"HTTP/1.0 404 "))
                    self.assertEqual(response.count(b"HTTP/1.0 "), 1)

    def test_existing_assets_have_correct_type_and_complete_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path, content, content_type in (
                ("/manifest.json", b'{"name":"SMP"}', b"application/manifest+json"),
                ("/sw.js", b"const version = 7;", b"application/javascript"),
            ):
                with self.subTest(path=path):
                    (root / path.lstrip("/")).write_bytes(content)
                    response = self.request_asset(root, path)
                    headers, body = response.split(b"\r\n\r\n", 1)
                    self.assertTrue(headers.startswith(b"HTTP/1.0 200 "))
                    self.assertIn(b"Content-Type: " + content_type, headers)
                    self.assertIn(b"Cache-Control: no-store", headers)
                    self.assertIn(f"Content-Length: {len(content)}".encode(), headers)
                    self.assertEqual(body, content)


if __name__ == "__main__":
    unittest.main()
