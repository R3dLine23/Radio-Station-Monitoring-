import gzip
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import islice

from radio_monitor.web import extract_now_playing, iter_web_metadata


BACKSLASH = "\\"


def flight_page(entries, split_at=None, extra_html=""):
    """Build a page shaped like kiss925.com: now_playing inside self.__next_f.push chunks.

    ``entries`` is a list of (call_letters, now_playing_dict_or_None).
    """
    rsc = '34:["$","$L34",null,{"nowPlayingPromise":"$@35","radioStation":{"id":529,"name":"KiSS 92.5"}}]\n'
    for i, (cl, np) in enumerate(entries):
        rsc += f"{35 + i}:" + json.dumps({"call_letters": cl, "now_playing": np}, separators=(",", ":")) + "\n"
    pieces = [rsc] if split_at is None else [rsc[:split_at], rsc[split_at:]]
    scripts = "".join(
        # Next.js writes "&" as \u0026 inside the inline script string.
        f"<script>self.__next_f.push([1,{json.dumps(p).replace('&', BACKSLASH + 'u0026')}])</script>"
        for p in pieces
    )
    return f"<!DOCTYPE html><html><head></head><body>{extra_html}{scripts}</body></html>"


KISS_NP = {"title": "I Go Dancing", "artist": "Frank Walker & Ella Henderson", "album": "",
           "image": {"thumbnail": {"src": "https://www.seekyoursounds.com/img/x.jpg"}}}
CHFI_NP = {"title": "Breakaway", "artist": "Kelly Clarkson", "album": "Breakaway"}


class ExtractTest(unittest.TestCase):
    def test_real_page_shape(self):
        np = extract_now_playing(flight_page([("CKIS", KISS_NP)]), "CKIS")
        self.assertEqual(np.artist, "Frank Walker & Ella Henderson")  # & decoded
        self.assertEqual(np.title, "I Go Dancing")
        self.assertEqual(np.display, "Frank Walker & Ella Henderson - I Go Dancing")

    def test_escaping_matches_the_live_site(self):
        page = flight_page([("CKIS", KISS_NP)])
        # Same bytes the discovery run showed in the real HTML.
        self.assertIn('\\"now_playing\\":{\\"title\\":\\"I Go Dancing\\"', page)
        self.assertIn("Frank Walker " + BACKSLASH + "u0026 Ella Henderson", page)

    def test_picks_the_right_station(self):
        page = flight_page([("CHFI", CHFI_NP), ("CKIS", KISS_NP)])
        self.assertEqual(extract_now_playing(page, "CKIS").title, "I Go Dancing")
        self.assertEqual(extract_now_playing(page, "chfi").title, "Breakaway")
        self.assertIsNone(extract_now_playing(page, "CJCL"))
        self.assertEqual(extract_now_playing(page).title, "Breakaway")  # no filter: first one

    def test_chunk_split_mid_object(self):
        page = flight_page([("CKIS", KISS_NP)], split_at=150)
        self.assertEqual(extract_now_playing(page, "CKIS").title, "I Go Dancing")

    def test_between_songs(self):
        np = extract_now_playing(flight_page([("CKIS", None)]), "CKIS")
        self.assertEqual(np.display, "")
        # Next.js serializes undefined as the string "$undefined".
        np = extract_now_playing(flight_page([("CKIS", "$undefined")]), "CKIS")
        self.assertIsNotNone(np)
        self.assertEqual(np.display, "")

    def test_prefers_entry_with_a_song(self):
        page = flight_page([("CKIS", "$undefined"), ("CKIS", KISS_NP)])
        self.assertEqual(extract_now_playing(page, "CKIS").title, "I Go Dancing")

    def test_plain_json(self):
        page = json.dumps({"call_letters": "CKIS", "now_playing": {"artist": "Backstreet Boys", "title": "The Call"}})
        self.assertEqual(extract_now_playing(page, "CKIS").display, "Backstreet Boys - The Call")

    def test_no_data(self):
        self.assertIsNone(extract_now_playing("<html><body>Hello</body></html>"))
        self.assertIsNone(extract_now_playing(flight_page([])))


class _Pages(BaseHTTPRequestHandler):
    pages: list = []
    paths: list = []

    def do_GET(self):
        _Pages.paths.append(self.path)
        body = _Pages.pages.pop(0) if len(_Pages.pages) > 1 else _Pages.pages[0]
        data = gzip.compress(body.encode())
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class PollTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Pages)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_polls_gzip_page_and_yields_songs(self):
        _Pages.paths = []
        _Pages.pages = [
            flight_page([("CKIS", KISS_NP)]),
            flight_page([("CKIS", {"artist": "Backstreet Boys", "title": "I Want It That Way"})]),
        ]
        items = list(islice(iter_web_metadata(self.url, 5, poll_seconds=0, call_letters="CKIS"), 3))
        self.assertEqual([m.title for m in items], [
            "Frank Walker & Ella Henderson - I Go Dancing",
            "Backstreet Boys - I Want It That Way",
            "Backstreet Boys - I Want It That Way",
        ])
        self.assertIn("Backstreet Boys", items[1].searchable_text)
        self.assertTrue(all("_=" in p for p in _Pages.paths), _Pages.paths)  # cache-busting

    def test_gaps_keep_polling_and_save_the_page(self):
        _Pages.pages = [
            "<html>no player data</html>",                  # station missing: no yield
            flight_page([("CKIS", "$undefined")]),          # between songs: heartbeat
            flight_page([("CKIS", {"artist": "Backstreet Boys", "title": "The Call"})]),
        ]
        with tempfile.TemporaryDirectory() as d:
            with self.assertLogs("radio_monitor.web", "WARNING") as logs:
                items = list(islice(iter_web_metadata(self.url, 5, poll_seconds=0,
                                                      call_letters="CKIS", debug_dir=d), 2))
            self.assertIsNone(items[0])
            self.assertEqual(items[1].title, "Backstreet Boys - The Call")
            saved = Path(d) / "CKIS-no-song.html"
            self.assertEqual(saved.read_text(), "<html>no player data</html>")
            self.assertTrue(any("no now_playing entry" in m for m in logs.output))

if __name__ == "__main__":
    unittest.main()
