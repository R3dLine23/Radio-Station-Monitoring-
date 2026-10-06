"""Read "now playing" metadata out of a Shoutcast/Icecast (ICY) audio stream.

The client asks for metadata with ``Icy-MetaData: 1``. The server then answers
with an ``icy-metaint: N`` header and, every N bytes of audio, inserts one length
byte L followed by L*16 bytes of metadata such as::

    StreamTitle='Backstreet Boys - I Want It That Way';StreamUrl='';

The server pushes this as soon as the song changes, so we don't have to poll.
We throw the audio bytes away and keep only the metadata.

Only the standard library is used. ``http.client`` doesn't accept the legacy
``ICY 200 OK`` status line that some Shoutcast servers send, so we patch that
one method.
"""

from __future__ import annotations

import http.client
import os
import re
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator

USER_AGENT = "radio-monitor/1.0 (+icy-metadata)"
_MAX_REDIRECTS = 5
_FIELD_RE = re.compile(r"(\w+)='(.*?)';(?=\s*\w+='|[\s\x00]*$)", re.DOTALL)


class StreamError(Exception):
    """The stream can't be opened or doesn't carry ICY metadata."""


@dataclass
class Metadata:
    """A single metadata block taken from the stream."""

    raw: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.fields.get("StreamTitle", "").strip()

    @property
    def searchable_text(self) -> str:
        """All field values combined, so matching works whichever field holds the artist."""
        return " ".join(v for v in self.fields.values() if v) or self.raw


def parse_metadata(block: bytes) -> Metadata:
    """Turn a raw metadata block into its key/value fields.

    Values may hold apostrophes ("Don't Go Breaking My Heart"), so a value
    ends only at a ``';`` that is followed by another key or by the end.
    """
    raw = _decode(block.rstrip(b"\x00")).strip()
    fields = {m.group(1): m.group(2) for m in _FIELD_RE.finditer(raw)}
    if not fields and raw.startswith("StreamTitle='"):
        # Malformed terminator: take everything after the opening quote.
        fields["StreamTitle"] = raw[len("StreamTitle='"):].rstrip("';")
    return Metadata(raw=raw, fields=fields)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


class _IcyHTTPResponse(http.client.HTTPResponse):
    """An HTTPResponse that also accepts ``ICY 200 OK`` as a status line."""

    def _read_status(self):  # type: ignore[override]
        if self.fp.peek(4)[:4] == b"ICY ":
            line = str(self.fp.readline(65537), "iso-8859-1")
            _, _, rest = line.partition(" ")
            status, _, reason = rest.strip().partition(" ")
            try:
                code = int(status)
            except ValueError as exc:
                raise http.client.BadStatusLine(line) from exc
            return "HTTP/1.0", code, reason
        return super()._read_status()


class _IcyHTTPConnection(http.client.HTTPConnection):
    response_class = _IcyHTTPResponse


class _IcyHTTPSConnection(http.client.HTTPSConnection):
    response_class = _IcyHTTPResponse


def _proxy_for(scheme: str, host: str) -> str | None:
    if urllib.request.proxy_bypass(host):
        return None
    for name in (f"{scheme}_proxy", f"{scheme.upper()}_PROXY"):
        if os.environ.get(name):
            return os.environ[name]
    return None


def _connect(url: str, timeout: float) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    for _ in range(_MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise StreamError(f"unsupported stream URL: {url}")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        proxy = _proxy_for(parts.scheme, parts.hostname)

        if parts.scheme == "https":
            ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)
            if proxy:
                p = urllib.parse.urlsplit(proxy)
                conn = _IcyHTTPSConnection(p.hostname, p.port or 80, timeout=timeout, context=ctx)
                conn.set_tunnel(parts.hostname, port)
            else:
                conn = _IcyHTTPSConnection(parts.hostname, port, timeout=timeout, context=ctx)
        else:
            if proxy:
                p = urllib.parse.urlsplit(proxy)
                conn = _IcyHTTPConnection(p.hostname, p.port or 80, timeout=timeout)
            else:
                conn = _IcyHTTPConnection(parts.hostname, port, timeout=timeout)

        path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        if parts.scheme == "http" and proxy:
            path = url  # A plain HTTP proxy expects the absolute URL.
        conn.request(
            "GET",
            path,
            headers={
                "Host": parts.netloc,
                "User-Agent": USER_AGENT,
                "Icy-MetaData": "1",
                "Accept": "*/*",
            },
        )
        resp = conn.getresponse()
        if resp.status in (301, 302, 303, 307, 308):
            location = resp.getheader("Location")
            conn.close()
            if not location:
                raise StreamError(f"redirect without Location from {url}")
            url = urllib.parse.urljoin(url, location)
            continue
        if resp.status != 200:
            conn.close()
            raise StreamError(f"HTTP {resp.status} {resp.reason} from {url}")
        return conn, resp
    raise StreamError(f"too many redirects for {url}")


def _read_exactly(resp: http.client.HTTPResponse, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = resp.read(min(remaining, 65536))
        if not chunk:
            raise StreamError("stream ended")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _skip(resp: http.client.HTTPResponse, n: int) -> None:
    remaining = n
    while remaining > 0:
        chunk = resp.read(min(remaining, 65536))
        if not chunk:
            raise StreamError("stream ended")
        remaining -= len(chunk)


def iter_metadata(url: str, timeout: float = 30.0) -> Iterator[Metadata | None]:
    """Connect to ``url`` and yield metadata for as long as the stream stays open.

    Yields a :class:`Metadata` for every non-empty metadata block, and ``None``
    for an empty block (the title hasn't changed). The ``None`` values act as a
    heartbeat that shows the stream is still alive. Raises :class:`StreamError`
    or ``OSError`` when the connection fails. Callers handle reconnects.
    """
    conn, resp = _connect(url, timeout)
    try:
        metaint_header = resp.getheader("icy-metaint")
        if not metaint_header:
            raise StreamError(
                f"{url} did not return an icy-metaint header, so it carries no song metadata"
            )
        try:
            metaint = int(metaint_header)
        except ValueError as exc:
            raise StreamError(f"bad icy-metaint header {metaint_header!r}") from exc
        if metaint <= 0:
            raise StreamError(f"bad icy-metaint header {metaint_header!r}")

        while True:
            _skip(resp, metaint)
            length = _read_exactly(resp, 1)[0] * 16
            if length == 0:
                yield None
                continue
            yield parse_metadata(_read_exactly(resp, length))
    finally:
        conn.close()

