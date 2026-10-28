"""Imagens anexadas as ordens do painel.

Os agentes de terminal nao recebem imagem colada pelo teclado, mas leem uma imagem pelo caminho do
arquivo. O painel salva a imagem em runtime/anexos/ (fora do Git) e manda o caminho junto com a ordem.
So aceita PNG, JPEG, GIF e WEBP, reconhecidos pelo conteudo (nao pela extensao informada).
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
KEEP_DAYS = 7


class AnexoError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def detect_image(data: bytes) -> str | None:
    """Extensao pelo cabecalho do arquivo, ou None se nao for uma imagem aceita."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def save_image(data: bytes, folder: Path, now: float | None = None) -> Path:
    if not data:
        raise AnexoError(400, "Imagem vazia.")
    if len(data) > MAX_BYTES:
        raise AnexoError(413, f"Imagem acima de {MAX_BYTES // (1024 * 1024)} MB.")
    ext = detect_image(data)
    if not ext:
        raise AnexoError(415, "Formato nao aceito. Use PNG, JPEG, GIF ou WEBP.")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    path = folder / f"print-{stamp}-{secrets.token_hex(3)}.{ext}"
    path.write_bytes(data)
    return path


def cleanup(folder: Path, max_age_days: int = KEEP_DAYS, now: float | None = None) -> int:
    """Apaga anexos antigos; devolve quantos foram removidos."""
    limit = (now or time.time()) - max_age_days * 86400
    removed = 0
    try:
        for f in folder.glob("print-*"):
            try:
                if f.is_file() and f.stat().st_mtime < limit:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed
