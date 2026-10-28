"""Historico da conversa de cada maestro, acumulado entre leituras.

O terminal de tela cheia (Claude Code) so guarda na tela as ultimas linhas: ao chegar uma
resposta nova, as antigas saem dela. Como o painel le a tela de tempos em tempos, aqui cada
ordem e cada resposta vista e guardada, em ordem, e atualizada enquanto a resposta ainda cresce.
Fica em runtime/conversas.json (fora do Git). So guarda texto de ordens e respostas ja visiveis
no terminal; nunca envia nada a ninguem.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

KEEP = 40


def _key(event: dict[str, Any]) -> str:
    if event["k"] == "ordem":
        return "o:" + event["x"][:80]
    items = event.get("items") or [{"x": ""}]
    return "r:" + items[0]["x"][:60]


class ConversationLog:
    def __init__(self, path: Path | None = None, keep: int = KEEP) -> None:
        self.path = path
        self.keep = keep
        self.data: dict[str, list[dict[str, Any]]] = {}
        if path:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data = loaded
            except (OSError, ValueError):
                pass

    def update(self, terminal_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stored = self.data.setdefault(terminal_id, [])
        last: int | None = None
        changed = False
        keys = [_key(e) for e in events]

        def find(key: str) -> int | None:
            return next((i for i, e in enumerate(stored) if e.get("key") == key), None)

        for n, ev in enumerate(events):
            entry = dict(ev, key=keys[n])
            idx = find(entry["key"])
            if idx is None and ev.get("parcial"):
                idx = self._merge_partial(stored, ev)
                if idx is not None:
                    last = idx
                    changed = True
                    continue
            if idx is None:
                if last is not None:
                    pos = last + 1  # logo depois da ultima ja conhecida
                else:
                    # ainda sem ancora: fica antes da primeira conhecida que vem adiante, ou no fim
                    later = next((p for p in (find(k) for k in keys[n + 1:]) if p is not None), None)
                    pos = later if later is not None else len(stored)
                stored.insert(pos, entry)
                last = pos
                changed = True
            else:
                if stored[idx] != entry:
                    stored[idx] = entry
                    changed = True
                last = idx
        if len(stored) > self.keep:
            del stored[: len(stored) - self.keep]
            changed = True
        if changed:
            self._save()
        return [{k: v for k, v in e.items() if k != "key"} for e in stored]

    @staticmethod
    def _merge_partial(stored: list[dict[str, Any]], ev: dict[str, Any]) -> int | None:
        """Trecho de resposta cujo comeco saiu da tela: completa a resposta ja guardada que o contem.

        O trecho comeca no meio de um paragrafo; procura-se onde ele recomeca na resposta guardada
        (mais recente primeiro) e se troca o resto pelo que a tela mostra agora.
        """
        items = ev.get("items") or []
        if not items:
            return None
        frag = items[0]["x"][:40]
        if len(frag) < 12:
            return None
        for i in range(len(stored) - 1, -1, -1):
            e = stored[i]
            if e.get("k") != "resp":
                continue
            for j, it in enumerate(e.get("items", [])):
                pos = it["x"].find(frag)
                if pos >= 0:
                    head = dict(it, x=it["x"][:pos] + items[0]["x"])
                    e["items"] = e["items"][:j] + [head] + items[1:]
                    return i
        return None

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass
