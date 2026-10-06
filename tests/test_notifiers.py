import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from radio_monitor.notifiers import Alert, NtfyNotifier, WebhookNotifier, build_notifiers


class _Capture(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Capture.requests.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


class NotifierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Capture)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Capture.requests.clear()

    ALERT = Alert(title="BACKSTREET BOYS on KISS 92.5!", message="Now playing: I Want It That Way ✨",
                  tags=["rotating_light"], click_url="sms:92592?&body=BSB")

    def test_ntfy(self):
        NtfyNotifier(topic="my-topic", server=self.base).send(self.ALERT)
        path, headers, body = _Capture.requests[0]
        self.assertEqual(path, "/my-topic")
        self.assertEqual(headers["Title"], "BACKSTREET BOYS on KISS 92.5!")
        self.assertEqual(headers["Priority"], "5")
        self.assertEqual(headers["Click"], "sms:92592?&body=BSB")
        self.assertEqual(headers["Tags"], "rotating_light")
        self.assertEqual(body.decode("utf-8"), self.ALERT.message)

    def test_webhook(self):
        WebhookNotifier(url=self.base + "/hook").send(self.ALERT)
        payload = json.loads(_Capture.requests[0][2])
        self.assertIn("I Want It That Way", payload["content"])
        self.assertEqual(payload["content"], payload["text"])

    def test_build_only_enabled(self):
        ns = build_notifiers({
            "console": {"enabled": True},
            "ntfy": {"enabled": False, "topic": ""},
        })
        self.assertEqual([n.name for n in ns], ["console"])
        with self.assertRaises(ValueError):
            build_notifiers({"ntfy": {"enabled": True, "topic": ""}})
        with self.assertRaises(ValueError):
            build_notifiers({"carrier_pigeon": {"enabled": True}})


if __name__ == "__main__":
    unittest.main()
