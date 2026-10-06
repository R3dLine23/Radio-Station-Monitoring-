"""A tiny local ICY radio server that tests point the real stream reader at."""

from __future__ import annotations

import socket
import threading


def metadata_block(title: str | None) -> bytes:
    if title is None:
        return b"\x00"
    payload = f"StreamTitle='{title}';StreamUrl='';".encode("utf-8")
    blocks = -(-len(payload) // 16)
    return bytes([blocks]) + payload.ljust(blocks * 16, b"\x00")


class FakeIcyServer:
    """Serves one stream per connection: the given titles, each followed by an empty block.

    ``status_line`` can be ``b"ICY 200 OK"`` (old Shoutcast) or
    ``b"HTTP/1.0 200 OK"``. Set ``chunked=True`` to test chunked transfer encoding.
    """

    def __init__(self, titles, metaint=32, status_line=b"HTTP/1.0 200 OK", chunked=False, send_metaint=True):
        self.titles = titles
        self.metaint = metaint
        self.status_line = status_line
        self.chunked = chunked
        self.send_metaint = send_metaint
        self.requests: list[bytes] = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/stream/icy"
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            req = b""
            while b"\r\n\r\n" not in req:
                data = conn.recv(4096)
                if not data:
                    return
                req += data
            self.requests.append(req)
            headers = [self.status_line, b"Content-Type: audio/aac"]
            if self.send_metaint:
                headers.append(f"icy-metaint: {self.metaint}".encode())
            if self.chunked:
                headers.append(b"Transfer-Encoding: chunked")
            conn.sendall(b"\r\n".join(headers) + b"\r\n\r\n")

            body = b""
            for t in self.titles:
                body += b"\xff" * self.metaint + metadata_block(t)
                body += b"\xff" * self.metaint + metadata_block(None)
            try:
                if self.chunked:
                    for i in range(0, len(body), 7):  # awkward chunk sizes on purpose
                        piece = body[i:i + 7]
                        conn.sendall(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                    conn.sendall(b"0\r\n\r\n")
                else:
                    conn.sendall(body)
            except OSError:
                pass

    def close(self):
        self.sock.close()
