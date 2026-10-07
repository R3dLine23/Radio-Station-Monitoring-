"""Read "now playing" from a station's website.

The Rogers station sites (kiss925.com, chfi.com) are Next.js apps. They render
the current song into the page's flight data like this::

    self.__next_f.push([1,"35:{\\"call_letters\\":\\"CKIS\\",\\"now_playing\\":{\\"title\\":\\"I Go Dancing\\",
        \\"artist\\":\\"Frank Walker \\u0026 Ella Henderson\\", ...}}"])

We fetch the page every few seconds, decode those chunks, and pull out the
``now_playing`` object that belongs to the station's call letters. The same
parser also works on plain JSON or HTML that has an unescaped
``"now_playing": {...}`` in it.
"""

from __future__ import annotations

import gzip
import json
import re
import time
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Iterator

from .icy import Metadata, StreamError

USER_AGENT = "Mozilla/5.0 (compatible; radio-monitor/1.1; +now-playing)"
_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', re.DOTALL)
_NOW_PLAYING_RE = re.compile(r'"now_playing"\s*:\s*')
_CALL_LETTERS_RE = re.compile(r'"call_letters"\s*:\s*"([^"]*)"')


@dataclass
class NowPlaying:
    artist: str
    title: str
    call_letters: str = ""

    @property
    def display(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        return self.artist or self.title


def fetch_page(url: str, timeout: float) -> str:
    # A changing query string keeps a CDN from handing back a stale copy.
    sep = "&" if urllib.parse.urlsplit(url).query else "?"
    req = urllib.request.Request(
        f"{url}{sep}_={int(time.time())}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        encoding = (resp.headers.get("Content-Encoding") or "").lower()
        charset = resp.headers.get_content_charset() or "utf-8"
    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding == "deflate":
        body = zlib.decompress(body)
    return body.decode(charset, "replace")


def _flight_text(html: str) -> str:
    """Join the decoded Next.js flight chunks (empty if the page has none)."""
    chunks = []
    for m in _PUSH_RE.finditer(html):
        try:
            chunks.append(json.loads(f'"{m.group(1)}"'))
        except ValueError:
            continue
    return "".join(chunks)


def _candidates(text: str) -> Iterator[NowPlaying | None]:
    decoder = json.JSONDecoder()
    for m in _NOW_PLAYING_RE.finditer(text):
        try:
            obj, _ = decoder.raw_decode(text, m.end())
        except ValueError:
            continue
        # The call letters sit just before now_playing in the same object.
        before = _CALL_LETTERS_RE.findall(text, max(0, m.start() - 400), m.start())
        call_letters = before[-1] if before else ""
        if obj is None:
            yield NowPlaying("", "", call_letters)  # between songs
        elif isinstance(obj, dict):
            yield NowPlaying(
                artist=str(obj.get("artist") or "").strip(),
                title=str(obj.get("title") or "").strip(),
                call_letters=call_letters,
            )


def extract_now_playing(page: str, call_letters: str = "") -> NowPlaying | None:
    """Find the current song in ``page``. Returns None if there's no now_playing data.

    When ``call_letters`` is given, only that station's entry is accepted, so a
    page that also lists sister stations never reports the wrong song.
    """
    for text in (_flight_text(page), page):
        if not text:
            continue
        for np in _candidates(text):
            if not call_letters or np.call_letters.upper() == call_letters.upper():
                return np
    return None


def iter_web_metadata(url: str, timeout: float = 30.0, poll_seconds: float = 15.0,
                      call_letters: str = "") -> Iterator[Metadata]:
    """Poll ``url`` forever, yielding the current song each time.

    Raises :class:`StreamError` when the page no longer contains now-playing
    data, so the monitor backs off, retries, and eventually sends a
    "monitoring is DOWN" alert instead of silently going blind.
    """
    while True:
        page = fetch_page(url, timeout)
        np = extract_now_playing(page, call_letters)
        if np is None:
            who = f" for {call_letters}" if call_letters else ""
            raise StreamError(f"no now_playing data{who} found at {url}")
        yield Metadata(
            raw=json.dumps({"artist": np.artist, "title": np.title, "call_letters": np.call_letters}),
            fields={"StreamTitle": np.display, "artist": np.artist, "title": np.title},
        )
        time.sleep(poll_seconds)
