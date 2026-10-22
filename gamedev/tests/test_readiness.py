import http.server
import threading
import unittest
import urllib.request

from gamedev.readiness import probe, wait_until_ready
from gamedev.tests import forbids_connection


class HealthyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/refused"):
            self.send_response(403)
            self.send_header("Content-Length", "6")
            self.end_headers()
            self.wfile.write(b"refuse")
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), HealthyHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/health/live/"
        forbids_connection(self, lambda: urllib.request.urlopen(self.url, timeout=5).close())

    def test_it_answers_when_the_workspace_is_up(self):
        self.assertTrue(wait_until_ready(self.url, timeout=10, interval=0.1))

    def test_it_fails_with_what_it_answered_when_nothing_is_listening(self):
        with self.assertRaises(TimeoutError) as raised:
            wait_until_ready("http://127.0.0.1:1/health/live/", timeout=1, interval=0.1)
        self.assertIn("did not answer", str(raised.exception))

    def test_a_probe_reports_the_reason_rather_than_raising(self):
        status, reason = probe("http://127.0.0.1:1/health/live/", timeout=1)
        self.assertIsNone(status)
        self.assertTrue(reason)

    def test_a_probe_reports_a_refusal(self):
        refused = self.url.replace("/health/live/", "/refused")
        status, reason = probe(refused, timeout=5)
        self.assertEqual(status, 403)
        self.assertEqual(reason, "HTTP 403")


if __name__ == "__main__":
    unittest.main()
