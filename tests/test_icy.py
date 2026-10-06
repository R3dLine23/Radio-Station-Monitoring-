import unittest
from itertools import islice

from radio_monitor.icy import StreamError, iter_metadata, parse_metadata

from .fake_icy_server import FakeIcyServer


class ParseTest(unittest.TestCase):
    def test_basic(self):
        m = parse_metadata(b"StreamTitle='Backstreet Boys - I Want It That Way';StreamUrl='';\x00\x00")
        self.assertEqual(m.title, "Backstreet Boys - I Want It That Way")
        self.assertEqual(m.fields["StreamUrl"], "")

    def test_apostrophes_in_title(self):
        m = parse_metadata(b"StreamTitle='Backstreet Boys - Everybody (Backstreet's Back)';StreamUrl='x';")
        self.assertEqual(m.title, "Backstreet Boys - Everybody (Backstreet's Back)")
        m = parse_metadata(b"StreamTitle='Don't Go';")
        self.assertEqual(m.title, "Don't Go")

    def test_latin1_fallback(self):
        m = parse_metadata("StreamTitle='Beyoncé';".encode("latin-1"))
        self.assertEqual(m.title, "Beyoncé")

    def test_artist_in_other_field_is_searchable(self):
        m = parse_metadata(b"StreamTitle='I Want It That Way';StreamUrl='artist=Backstreet Boys';")
        self.assertIn("Backstreet Boys", m.searchable_text)

    def test_unterminated(self):
        self.assertEqual(parse_metadata(b"StreamTitle='Half a title").title, "Half a title")


class StreamTest(unittest.TestCase):
    TITLES = ["Taylor Swift - Shake It Off", "Backstreet Boys - Everybody (Backstreet's Back)"]

    def _read(self, server, n=4):
        try:
            return list(islice(iter_metadata(server.url, timeout=5), n))
        finally:
            server.close()

    def _titles(self, items):
        return [m.title if m else None for m in items]

    def test_http_status_line(self):
        s = FakeIcyServer(self.TITLES)
        items = self._read(s)
        self.assertEqual(self._titles(items), [self.TITLES[0], None, self.TITLES[1], None])
        self.assertIn(b"Icy-MetaData: 1", s.requests[0])

    def test_legacy_icy_status_line(self):
        items = self._read(FakeIcyServer(self.TITLES, status_line=b"ICY 200 OK"))
        self.assertEqual(self._titles(items)[::2], self.TITLES)

    def test_chunked(self):
        items = self._read(FakeIcyServer(self.TITLES, chunked=True, metaint=50))
        self.assertEqual(self._titles(items)[::2], self.TITLES)

    def test_no_metaint_is_an_error(self):
        with self.assertRaises(StreamError):
            self._read(FakeIcyServer(self.TITLES, send_metaint=False))

    def test_stream_end_raises(self):
        s = FakeIcyServer(["only one"])
        with self.assertRaises(StreamError):
            list(iter_metadata(s.url, timeout=5))
        s.close()


if __name__ == "__main__":
    unittest.main()
