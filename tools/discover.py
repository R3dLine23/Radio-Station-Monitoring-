"""Find where a station publishes "now playing" song info.

Run from the repo root (it needs open internet, e.g. Google Cloud Shell or the VM):

    python3 tools/discover.py | tee discover.txt

It checks three possible sources at once and prints what each one returns:
  1. ICY stream metadata over several minutes (does the title ever change?)
  2. ID3 tags inside the HLS audio segments
  3. "now playing" / playlist API URLs referenced by the station websites
"""

from __future__ import annotations

import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radio_monitor.icy import _connect, _read_exactly, _skip, parse_metadata  # noqa: E402

STATIONS = {
    "KISS 92.5": {"slug": "tor925", "site": "https://www.kiss925.com/"},
    "CHFI 98.1": {"slug": "tor981", "site": "https://www.chfi.com/"},
}
ICY_URL = "https://rogers-hls.leanstream.co/rogers/{slug}.stream/icy"
HLS_URL = "https://rogers-hls.leanstream.co/rogers/{slug}.stream/playlist.m3u8"
WATCH_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
UA = "Mozilla/5.0 (radio-monitor discovery)"
API_HINT = re.compile(
    r"now.?playing|recently.?played|on.?air|playlist|songs?|tracks?|/api/|\.json|"
    r"leanstream|leanplayer|triton|streamtheworld|amperwave|stream\.|metadata|history",
    re.I,
)

_print_lock = threading.Lock()


def out(section: str, msg: str) -> None:
    with _print_lock:
        print(f"[{time.strftime('%H:%M:%S')}] [{section}] {msg}", flush=True)


def fetch(url: str, limit: int = 3_000_000) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read(limit)


# ---- 1. ICY metadata over time ------------------------------------------------

def watch_icy(name: str, slug: str) -> None:
    sec = f"ICY {name}"
    url = ICY_URL.format(slug=slug)
    seen: list[str] = []
    try:
        conn, resp = _connect(url, 30)
    except Exception as exc:  # noqa: BLE001
        out(sec, f"connect FAILED: {exc}")
        return
    try:
        headers = {k: v for k, v in resp.getheaders() if k.lower().startswith(("icy", "content-type", "ice"))}
        out(sec, f"headers: {headers}")
        metaint = int(resp.getheader("icy-metaint"))
        deadline = time.monotonic() + WATCH_SECONDS
        while time.monotonic() < deadline:
            _skip(resp, metaint)
            length = _read_exactly(resp, 1)[0] * 16
            if length:
                raw = parse_metadata(_read_exactly(resp, length)).raw
                if not seen or raw != seen[-1]:
                    seen.append(raw)
                    out(sec, f"metadata: {raw}")
    except Exception as exc:  # noqa: BLE001
        out(sec, f"stream error: {exc}")
    finally:
        conn.close()
        distinct = len(set(seen))
        out(sec, f"DONE: {distinct} distinct value(s) "
                 + ("-> titles DO change" if distinct > 1 else "-> title never changed"))


# ---- 2. ID3 tags in HLS segments ----------------------------------------------

def _id3_frames(data: bytes) -> list[str]:
    found = []
    for m in re.finditer(rb"ID3[\x03\x04]", data):
        i = m.start()
        hdr = data[i:i + 10]
        if len(hdr) < 10:
            continue
        size = (hdr[6] << 21) | (hdr[7] << 14) | (hdr[8] << 7) | hdr[9]
        body = data[i + 10:i + 10 + size]
        j = 0
        while j + 10 <= len(body):
            fid = body[j:j + 4]
            if not re.fullmatch(rb"[A-Z0-9]{4}", fid):
                break
            fsize = int.from_bytes(body[j + 4:j + 8], "big")
            if hdr[3] == 4:  # v2.4 uses syncsafe sizes
                b = body[j + 4:j + 8]
                fsize = (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]
            payload = body[j + 10:j + 10 + fsize]
            text = re.sub(rb"[^\x20-\x7e]+", b" ", payload).decode().strip()
            found.append(f"{fid.decode()}={text[:200]!r}")
            j += 10 + fsize
    return found


def probe_hls(name: str, slug: str) -> None:
    sec = f"HLS {name}"
    try:
        url = HLS_URL.format(slug=slug)
        for _ in range(3):  # master -> media playlist
            status, ctype, body = fetch(url)
            text = body.decode("utf-8", "replace")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            tags = [ln for ln in lines if ln.startswith("#") and not ln.startswith(("#EXTINF:", "#EXT-X-PROGRAM"))]
            out(sec, f"{url} -> HTTP {status}, {len(lines)} lines; tags: {sorted(set(t.split(':')[0] for t in tags))}")
            for ln in lines:
                if ln.startswith(("#EXTINF", "#EXT-X-DATERANGE", "#EXT-X-PROGRAM-DATE-TIME")) and "," in ln and len(ln) > 12:
                    out(sec, f"  sample tag: {ln[:200]}")
                    break
            uris = [ln for ln in lines if not ln.startswith("#")]
            if not uris:
                return
            nxt = urllib.parse.urljoin(url, uris[-1])
            if ".m3u8" in nxt:
                url = nxt
                continue
            # A media playlist: inspect the newest couple of segments.
            for seg in uris[-2:]:
                seg_url = urllib.parse.urljoin(url, seg)
                s, ct, data = fetch(seg_url)
                frames = _id3_frames(data)
                out(sec, f"segment {seg_url.rsplit('/', 1)[-1]}: HTTP {s}, {ct}, {len(data)} bytes, "
                         f"ID3 frames: {frames if frames else 'none'}")
            return
    except Exception as exc:  # noqa: BLE001
        out(sec, f"FAILED: {exc}")


# ---- 3. Website now-playing APIs ----------------------------------------------

URL_RE = re.compile(r"""(?:https?:)?//[^\s"'<>()\\]+|["'](/[^\s"'<>()\\]{3,200})["']""")


def _label(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1][:40] if urllib.parse.urlsplit(url).path.strip("/") else "homepage"


def probe_site(name: str, site: str) -> None:
    sec = f"WEB {name}"
    try:
        status, _, body = fetch(site)
    except Exception as exc:  # noqa: BLE001
        out(sec, f"{site} FAILED: {exc}")
        return
    html = body.decode("utf-8", "replace")
    out(sec, f"{site} -> HTTP {status}, {len(html)} chars")
    host = urllib.parse.urlsplit(site).netloc
    scripts = [urllib.parse.urljoin(site, s) for s in re.findall(r"<script[^>]+src=[\"']([^\"']+)", html)]
    texts = {site: html}
    for s in scripts[:25]:
        if host in s or s.startswith(site) or "rogers" in s:
            try:
                texts[s] = fetch(s)[2].decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
    hits: dict[str, str] = {}
    for src, text in texts.items():
        for m in URL_RE.finditer(text):
            u = m.group(1) or m.group(0)
            if API_HINT.search(u) and not re.search(r"\.(png|jpe?g|gif|svg|webp|css|woff2?)(\?|$)", u, re.I):
                hits.setdefault(u[:220], _label(src))
    out(sec, f"checked {len(texts)} file(s); {len(hits)} candidate URL(s):")
    for u, src in list(hits.items())[:60]:
        out(sec, f"  {u}    (in {src})")
    for kw in ("nowPlaying", "now_playing", "nowplaying", "recentlyPlayed", "songHistory", "onAir"):
        for src, text in texts.items():
            i = text.find(kw)
            if i >= 0:
                out(sec, f"  keyword {kw!r} in {_label(src)}: ...{text[max(0, i-120):i+160]!r}...")
                break


def main() -> None:
    print(f"Discovery run: watching ICY for {WATCH_SECONDS}s while probing HLS and websites.\n", flush=True)
    threads = [threading.Thread(target=watch_icy, args=(n, s["slug"])) for n, s in STATIONS.items()]
    for t in threads:
        t.start()
    for n, s in STATIONS.items():
        probe_hls(n, s["slug"])
    for n, s in STATIONS.items():
        probe_site(n, s["site"])
    out("main", f"web/HLS checks done; waiting for the ICY watch to finish (up to {WATCH_SECONDS}s)...")
    for t in threads:
        t.join()
    out("main", "all done")


if __name__ == "__main__":
    main()
