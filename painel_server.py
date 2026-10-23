"""Servidor local do painel: serve runtime/painel.html e envia ao andar a proposta aceita.

Rode a partir de um terminal Maestri (herda o ambiente do CLI `maestri`):
    python -I D:/Monitor/painel_server.py
Abra http://127.0.0.1:8765/ (porta em monitor-config.json, chave painel_porta).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "monitor-config.json"
RUNTIME = ROOT / "runtime"
ITEM_RE = re.compile(r"^R\d{2}$")
FALLBACK_CLI = "C:/Users/samuel.almeida/AppData/Local/Programs/Maestri/resources/cli/maestri.exe"
_LOCK = threading.Lock()


class AcceptError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_message(sender: str, proposal: dict[str, Any]) -> str:
    deps = ", ".join(proposal.get("deps") or []) or "nenhuma"
    return (
        f"[{sender}] Demanda aceita pelo usuario no painel: {proposal['item']} {proposal.get('title', '')}".rstrip()
        + f" (dependencias: {deps}). Siga o fluxo do projeto: reserve o item no docs/roadmap.md"
        " (commit so da linha + push na origin/dev) antes de codar e use /spec, /melhoria ou /bug conforme o caso."
        " Responda em 2 linhas quando iniciar."
    )


def accept(item: str, floor: str, runtime: Path = RUNTIME, config_path: Path = CONFIG,
           runner: Callable[..., Any] = subprocess.run, background: bool = True) -> dict[str, Any]:
    """Valida o pedido contra a proposta atual e envia ao orquestrador do andar."""
    if not ITEM_RE.match(item or ""):
        raise AcceptError(400, "item invalido")
    config = _read(config_path, {})
    floor_cfg = next((f for f in config.get("floors", []) if f.get("name") == floor), None)
    if not floor_cfg or not floor_cfg.get("orquestrador"):
        raise AcceptError(400, "andar invalido")
    proposals = _read(runtime / "propostas.json", {}).get("proposals", [])
    proposal = next((p for p in proposals if p.get("item") == item and p.get("floor") == floor), None)
    if proposal is None:
        raise AcceptError(409, "proposta nao esta mais ativa; recarregue o painel")
    with _LOCK:
        accepted = _read(runtime / "aceitas.json", {})
        if accepted.get(item, {}).get("status") in ("enviando", "enviado"):
            raise AcceptError(409, "demanda ja enviada")
        accepted[item] = {"floor": floor, "status": "enviando", "at": datetime.now(timezone.utc).isoformat()}
        _write(runtime / "aceitas.json", accepted)
    message = build_message(config.get("remetente", "Monitor de Andares"), proposal)
    target = floor_cfg["orquestrador"]

    def send() -> None:
        cli = os.environ.get("MAESTRI_CLI") or shutil.which("maestri") or FALLBACK_CLI
        status, detail = "enviado", ""
        try:
            result = runner([cli, "ask", target, message], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=600)
            if result.returncode != 0:
                status, detail = "falhou", (result.stderr or result.stdout or "")[:300]
        except (OSError, subprocess.SubprocessError) as error:
            status, detail = "falhou", str(error)[:300]
        with _LOCK:
            current = _read(runtime / "aceitas.json", {})
            current[item] = {**current.get(item, {}), "status": status, "detail": detail,
                             "done_at": datetime.now(timezone.utc).isoformat()}
            _write(runtime / "aceitas.json", current)

    if background:
        threading.Thread(target=send, daemon=True).start()
    else:
        send()
    return {"item": item, "floor": floor, "target": target, "status": "enviando"}


class Handler(BaseHTTPRequestHandler):
    server_version = "PainelMonitor/1"

    def log_message(self, *args: Any) -> None:  # silencioso
        pass

    def _local_host(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: Any) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._local_host():
            return self._json(403, {"erro": "host nao permitido"})
        if self.path in ("/", "/painel.html"):
            try:
                body = (RUNTIME / "painel.html").read_bytes()
            except OSError:
                return self._json(404, {"erro": "painel ainda nao gerado"})
            return self._send(200, body, "text/html; charset=utf-8")
        if self.path == "/aceitas":
            return self._json(200, _read(RUNTIME / "aceitas.json", {}))
        self._json(404, {"erro": "nao encontrado"})

    def do_POST(self) -> None:
        if not self._local_host() or self.headers.get("X-Painel") != "1":
            return self._json(403, {"erro": "pedido nao permitido"})
        if self.path != "/aceitar":
            return self._json(404, {"erro": "nao encontrado"})
        try:
            size = min(int(self.headers.get("Content-Length") or 0), 4096)
            data = json.loads(self.rfile.read(size) or b"{}")
            result = accept(str(data.get("item", "")), str(data.get("floor", "")))
        except AcceptError as error:
            return self._json(error.status, {"erro": error.message})
        except (ValueError, TypeError):
            return self._json(400, {"erro": "corpo invalido"})
        self._json(202, result)


def main() -> int:
    port = int(_read(CONFIG, {}).get("painel_porta", 8765))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Painel em http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
