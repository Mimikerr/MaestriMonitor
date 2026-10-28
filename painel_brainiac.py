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
from conversa import ConversationLog

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "painel_brainiac.html"
CACHE_SECONDS = 2
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "body": b"{}"}
_log = ConversationLog(ROOT / "runtime" / "conversas.json")


def snapshot_json() -> bytes:
    with _lock:
        if time.monotonic() - _cache["at"] > CACHE_SECONDS:
            data = source.build_snapshot(source.load_config(), log=_log)
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

    def _origin_ok(self) -> bool:
        """So aceita pedidos da propria pagina local (bloqueia sites de fora chamando 127.0.0.1)."""
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return (self.headers.get("Host") in hosts and self.headers.get("X-Painel") == "1"
                and (origin is None or origin in {f"http://{h}" for h in hosts}))

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/enviar":
            self._send(404, b"nao encontrado", "text/plain; charset=utf-8")
            return
        if not self._origin_ok():
            self._send_json(403, {"ok": False, "erro": "Pedido recusado."})
            return
        try:
            size = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(size, 16384)) or b"{}")
            result = source.send_to_maestro(source.load_config(), str(body.get("projeto", "")), body.get("mensagem"))
            self._log_send(result)
            self._send_json(200, result)
        except source.SendError as exc:
            self._send_json(exc.status, {"ok": False, "erro": exc.message})
        except (ValueError, TypeError):
            self._send_json(400, {"ok": False, "erro": "Pedido invalido."})

    def _send_json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    @staticmethod
    def _log_send(result: dict) -> None:
        """Registra so quando, para quem e o tamanho; nunca o texto da ordem."""
        try:
            runtime = ROOT / "runtime"
            runtime.mkdir(exist_ok=True)
            with (runtime / "enviados.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"em": time.strftime("%Y-%m-%dT%H:%M:%S"), "projeto": result["projeto"],
                                    "maestro": result["maestro"], "tamanho": result["tamanho"]}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def log_message(self, *_args) -> None:
        pass


def _poll() -> None:
    """Le o Brainiac de tempos em tempos mesmo sem navegador aberto, para nao perder respostas
    que saem da tela do terminal."""
    while True:
        try:
            snapshot_json()
        except Exception:  # noqa: BLE001 - o leitor nunca derruba o servidor
            pass
        time.sleep(3)


def main() -> int:
    port = int(source.load_config().get("porta", 8766))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Painel em http://127.0.0.1:{port}/")
    threading.Thread(target=_poll, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
