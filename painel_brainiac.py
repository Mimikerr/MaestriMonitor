"""Painel central (somente leitura) dos projetos, alimentado pelo Brainiac.

    python painel_brainiac.py            # http://127.0.0.1:8766/
Porta e projetos em projetos.json. Nao envia comando a agente nem altera repositorio.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import brainiac_source as source

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "painel_brainiac.html"
CACHE_SECONDS = 5
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "body": b"{}"}


def snapshot_json() -> bytes:
    with _lock:
        if time.monotonic() - _cache["at"] > CACHE_SECONDS:
            data = source.build_snapshot(source.load_config())
            _cache.update(at=time.monotonic(), body=json.dumps(data, ensure_ascii=False).encode("utf-8"))
        return _cache["body"]


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/snapshot":
            self._send(200, snapshot_json(), "application/json; charset=utf-8")
        else:
            self._send(404, b"nao encontrado", "text/plain; charset=utf-8")

    def log_message(self, *_args) -> None:
        pass


def main() -> int:
    port = int(source.load_config().get("porta", 8766))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Painel em http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
