"""Reconstroi o texto visivel de um terminal a partir do scrollback cru (sequencias ANSI).

Programas de tela cheia (Claude Code, Codex) redesenham com posicionamento de cursor; apenas
remover os codigos junta as palavras. Aqui o fluxo e reproduzido numa tela virtual (cols x rows)
e o resultado sao as linhas como o usuario as ve. Somente leitura; cores e estilos sao ignorados.
"""
from __future__ import annotations

import re
import unicodedata

_CSI = re.compile(r"\x1b\[([?>=!]?)([0-9;:]*)([ -/]*)([@-~])")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC2 = re.compile(r"\x1b[()*+][A-Za-z0-9]|\x1b[=>78DEHMNOZc]")
HISTORY = 400


def _width(ch: str) -> int:
    if unicodedata.combining(ch) or ch in "​‍️":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class Screen:
    def __init__(self, cols: int = 120, rows: int = 40) -> None:
        self.cols = max(20, min(int(cols or 120), 400))
        self.rows = max(5, min(int(rows or 40), 200))
        self.grid = [[" "] * self.cols for _ in range(self.rows)]
        self.history: list[str] = []
        self.x = self.y = 0
        self.pending = False
        self.top, self.bottom = 0, self.rows - 1
        self.saved = (0, 0)
        self.alt = False

    # --- operacoes basicas ---
    def _blank(self) -> list[str]:
        return [" "] * self.cols

    def _scroll_up(self, n: int = 1) -> None:
        for _ in range(n):
            gone = self.grid.pop(self.top)
            if self.top == 0 and not self.alt:
                self.history.append("".join(gone).rstrip())
                del self.history[:-HISTORY]
            self.grid.insert(self.bottom, self._blank())

    def _scroll_down(self, n: int = 1) -> None:
        for _ in range(n):
            self.grid.pop(self.bottom)
            self.grid.insert(self.top, self._blank())

    def _linefeed(self) -> None:
        if self.y == self.bottom:
            self._scroll_up()
        elif self.y < self.rows - 1:
            self.y += 1

    def _put(self, ch: str) -> None:
        w = _width(ch)
        if w == 0:
            return
        if self.pending or self.x + w > self.cols:
            self.x = 0
            self._linefeed()
            self.pending = False
        self.grid[self.y][self.x] = ch
        if w == 2 and self.x + 1 < self.cols:
            self.grid[self.y][self.x + 1] = ""
        self.x += w
        if self.x >= self.cols:
            self.x = self.cols - 1
            self.pending = True

    # --- CSI ---
    def _csi(self, private: str, params: str, final: str) -> None:
        if private:
            # tela alternativa (?1049h): app de tela cheia, sem historico de rolagem proprio do terminal
            if final in "hl" and any(x in ("1049", "47", "1047") for x in re.split(r"[;:]", params)):
                self.alt = final == "h"
                if self.alt:
                    self.history.clear()
                    self.grid = [self._blank() for _ in range(self.rows)]
                    self.x = self.y = 0
                    self.pending = False
            return  # os demais modos (?25h...) nao mudam o texto
        nums = [int(p) if p.isdigit() else 0 for p in re.split(r"[;:]", params)] if params else []
        a = nums[0] if nums and nums[0] else 1
        self.pending = False
        if final == "A":
            self.y = max(self.top if self.y >= self.top else 0, self.y - a)
        elif final in "Be":
            self.y = min(self.rows - 1, self.y + a)
        elif final in "Ca":
            self.x = min(self.cols - 1, self.x + a)
        elif final == "D":
            self.x = max(0, self.x - a)
        elif final == "E":
            self.x, self.y = 0, min(self.rows - 1, self.y + a)
        elif final == "F":
            self.x, self.y = 0, max(0, self.y - a)
        elif final in "G`":
            self.x = min(self.cols - 1, max(0, a - 1))
        elif final == "d":
            self.y = min(self.rows - 1, max(0, a - 1))
        elif final in "Hf":
            r = nums[0] if len(nums) > 0 and nums[0] else 1
            c = nums[1] if len(nums) > 1 and nums[1] else 1
            self.y, self.x = min(self.rows - 1, r - 1), min(self.cols - 1, c - 1)
        elif final == "J":
            mode = nums[0] if nums else 0
            if mode == 0:
                self._erase_line(self.x, self.cols)
                for r in range(self.y + 1, self.rows):
                    self.grid[r] = self._blank()
            elif mode == 1:
                self._erase_line(0, self.x + 1)
                for r in range(0, self.y):
                    self.grid[r] = self._blank()
            else:
                for r in range(self.rows):
                    self.grid[r] = self._blank()
        elif final == "K":
            mode = nums[0] if nums else 0
            self._erase_line(*((self.x, self.cols) if mode == 0 else (0, self.x + 1) if mode == 1 else (0, self.cols)))
        elif final == "X":
            self._erase_line(self.x, min(self.cols, self.x + a))
        elif final == "P":
            row = self.grid[self.y]
            del row[self.x:self.x + a]
            row.extend([" "] * (self.cols - len(row)))
        elif final == "@":
            row = self.grid[self.y]
            row[self.x:self.x] = [" "] * a
            del row[self.cols:]
        elif final == "L" and self.top <= self.y <= self.bottom:
            for _ in range(a):
                self.grid.pop(self.bottom)
                self.grid.insert(self.y, self._blank())
        elif final == "M" and self.top <= self.y <= self.bottom:
            for _ in range(a):
                self.grid.pop(self.y)
                self.grid.insert(self.bottom, self._blank())
        elif final == "S":
            self._scroll_up(a)
        elif final == "T":
            self._scroll_down(a)
        elif final == "r":
            t = nums[0] if nums and nums[0] else 1
            b = nums[1] if len(nums) > 1 and nums[1] else self.rows
            if 1 <= t < b <= self.rows:
                self.top, self.bottom = t - 1, b - 1
            self.x = self.y = 0
        elif final == "s":
            self.saved = (self.x, self.y)
        elif final == "u":
            self.x, self.y = self.saved

    def _erase_line(self, start: int, end: int) -> None:
        row = self.grid[self.y]
        for i in range(max(0, start), min(self.cols, end)):
            row[i] = " "

    # --- entrada ---
    def feed(self, data: str) -> None:
        i, n = 0, len(data)
        while i < n:
            ch = data[i]
            if ch == "\x1b":
                m = _CSI.match(data, i)
                if m:
                    self._csi(m.group(1), m.group(2), m.group(4))
                    i = m.end()
                    continue
                m = _OSC.match(data, i) or _ESC2.match(data, i)
                if m:
                    tail = m.group(0)
                    if tail == "\x1b7":
                        self.saved = (self.x, self.y)
                    elif tail == "\x1b8":
                        self.x, self.y = self.saved
                    elif tail == "\x1bM":
                        if self.y == self.top:
                            self._scroll_down()
                        else:
                            self.y = max(0, self.y - 1)
                    elif tail == "\x1bD":
                        self._linefeed()
                    elif tail == "\x1bE":
                        self.x = 0
                        self._linefeed()
                    i = m.end()
                    continue
                i += 1
                continue
            if ch == "\n":
                self._linefeed()
                self.pending = False
            elif ch == "\r":
                self.x = 0
                self.pending = False
            elif ch == "\b":
                self.x = max(0, self.x - 1)
                self.pending = False
            elif ch == "\t":
                self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
            elif ch >= " " and ch != "\x7f":
                self._put(ch)
            i += 1

    def lines(self) -> list[str]:
        screen = ["".join(c for c in row).rstrip() for row in self.grid]
        while screen and not screen[-1]:
            screen.pop()
        return self.history + screen


def render_terminal(raw: str, cols: int = 120, rows: int = 40, keep: int = 16) -> list[str]:
    """Ultimas `keep` linhas nao vazias do terminal, com a tela reconstruida."""
    scr = Screen(cols, rows)
    scr.feed(raw or "")
    out: list[str] = []
    for ln in scr.lines():
        if re.fullmatch(r"[─━═\-_ ]{12,}", ln):
            ln = "─" * 24
        if ln.strip() or (out and out[-1].strip()):
            out.append(ln[:240])
    while out and not out[-1].strip():
        out.pop()
    return out[-keep:]


_BULLET = re.compile(r"^[●•]\s+(.*)$")
_TOOL_CALL = re.compile(r"^[A-Z][A-Za-z]+\(.*")
_TOOL_SUMMARY = re.compile(r"^\s*(Listed|Ran|Read|Searched|Wrote|Edited|Fetched|Created|Updated|Explored|Called|Found|Searching|Reading|Running|Listing)\b")
MAX_INDENT = 8


def extract_blocks(lines: list[str]) -> list[list[str]]:
    """Cada resposta do agente (bloco que comeca com a bolinha) como lista de linhas, sem o ruido da interface."""
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for ln in lines:
        m = _BULLET.match(ln)
        if m:
            cur = None
            if not _TOOL_CALL.match(m.group(1)):
                cur = [m.group(1)]
                blocks.append(cur)
            continue
        if cur is None:
            continue
        if not ln.strip():
            cur.append("")
            continue
        indent = len(ln) - len(ln.lstrip(" "))
        if indent == 0 or _TOOL_SUMMARY.match(ln):
            cur = None
        elif indent <= MAX_INDENT:
            cur.append(ln[2:] if ln.startswith("  ") else ln)
        # indent grande = aviso alinhado a direita da interface: ignora sem fechar o bloco
    for b in blocks:
        while b and not b[-1].strip():
            b.pop()
    return [b for b in blocks if b]


def extract_responses(lines: list[str], keep: int = 60) -> list[str]:
    """Respostas do agente em linhas corridas (separadas por uma linha vazia)."""
    out: list[str] = []
    for b in extract_blocks(lines):
        if out:
            out.append("")
        out.extend(b)
    return out[-keep:]


_ITEM = re.compile(r"^(?:([-*•])|(\d{1,2}[.)]))\s+(.*)$")


def structure_block(lines: list[str]) -> list[dict[str, str]]:
    """Desfaz a quebra de linha do terminal: paragrafos (`p`) e itens de lista (`li`)."""
    items: list[dict[str, str]] = []
    after_blank = True
    for raw in lines:
        text = raw.strip()
        if not text:
            after_blank = True
            continue
        m = _ITEM.match(text)
        if m:
            items.append({"t": "li", "n": m.group(2) or "", "x": m.group(3)})
        elif items and not after_blank:
            items[-1]["x"] += " " + text
        else:
            items.append({"t": "p", "n": "", "x": text})
        after_blank = False
    return items


def render_responses(raw: str, cols: int = 120, rows: int = 40, keep: int = 60) -> list[str]:
    scr = Screen(cols, rows)
    scr.feed(raw or "")
    return extract_responses([ln[:240] for ln in scr.lines()], keep)


def render_response_cards(raw: str, cols: int = 120, rows: int = 40, keep: int = 6) -> list[list[dict[str, str]]]:
    """As ultimas `keep` respostas do agente, cada uma ja estruturada em paragrafos e listas."""
    scr = Screen(cols, rows)
    scr.feed(raw or "")
    blocks = extract_blocks([ln[:240] for ln in scr.lines()])
    return [structure_block(b) for b in blocks[-keep:]]


_SEP = re.compile(r"^[─━═\-]{10,}\s*$")
_PROMPT = re.compile(r"^❯\s*(.*)$")
_SPINNER = re.compile(r"^\s*[✻✢✶✳✽·*]\s+(\S.*(?:esc to interrupt|…\s*\(\d+s|\(\d+s\s*·).*)$")


def _neighbor(lines: list[str], i: int, step: int) -> str:
    j = i + step
    while 0 <= j < len(lines) and not lines[j].strip():
        j += step
    return lines[j] if 0 <= j < len(lines) else ""


_NOISE = re.compile(r"^(How is Claude doing|Tip:|Update available|Press Ctrl-C again|Interrupted)")
_TOOL_MARK = ("⎿", "└")  # ⎿ e └: saida de ferramenta
_STATUS = re.compile(r"^(Running|Listing|Reading|Searching|Fetching|Writing|Editing|Updating|Creating|Waiting|Thinking|Searched|Read|Ran)\b.*(…|\(ctrl\+o to expand\))\s*$")


def extract_events(lines: list[str], partial_top: bool = False) -> list[dict]:
    """Ordens enviadas (`❯ texto`, fora da caixa de entrada) e respostas, na ordem da tela.

    Chamadas de ferramenta (bolinha com saida `⎿`, "Running 1 shell command…") nao sao respostas.
    Com `partial_top`, o texto recuado no topo da tela (resposta cuja bolinha ja saiu da tela)
    vira uma resposta marcada como `parcial`.
    """
    events: list[dict] = []
    cur: dict | None = None
    started = False
    for i, raw in enumerate(lines):
        ln = raw.replace(" ", " ")
        first = not started and bool(ln.strip())
        if first:
            started = True
        pm = _PROMPT.match(ln)
        if pm:
            if _SEP.match(_neighbor(lines, i, -1)) and _SEP.match(_neighbor(lines, i, 1)):
                cur = None  # caixa de entrada atual, nao e uma ordem ja enviada
                continue
            cur = {"k": "ordem", "lines": [pm.group(1).strip()]} if pm.group(1).strip() else None
            if cur:
                events.append(cur)
            continue
        bm = _BULLET.match(ln)
        if bm:
            cur = None
            if not _TOOL_CALL.match(bm.group(1)):
                cur = {"k": "resp", "lines": [bm.group(1)]}
                events.append(cur)
            continue
        if cur is None:
            indent = len(ln) - len(ln.lstrip(" "))
            if (first and partial_top and 2 <= indent <= MAX_INDENT and not _TOOL_SUMMARY.match(ln)
                    and not ln.strip().startswith(_TOOL_MARK)):
                cur = {"k": "resp", "lines": [ln[2:]], "parcial": True}
                events.append(cur)
            continue
        if not ln.strip():
            if cur["k"] == "ordem":
                cur = None
            else:
                cur["lines"].append("")
            continue
        indent = len(ln) - len(ln.lstrip(" "))
        if ln.strip().startswith(_TOOL_MARK):
            if cur["k"] == "resp":
                cur["tool"] = True  # a "resposta" era uma chamada de ferramenta
            continue
        if indent == 0 or _TOOL_SUMMARY.match(ln):
            cur = None
        elif indent <= MAX_INDENT:
            cur["lines"].append(ln[2:] if ln.startswith("  ") else ln)
    out = []
    for e in events:
        while e["lines"] and not e["lines"][-1].strip():
            e["lines"].pop()
        if not e["lines"] or e.get("tool") or _STATUS.match(e["lines"][0]) or _NOISE.match(e["lines"][0]):
            continue
        out.append(e)
    return out


def detect_activity(lines: list[str]) -> str:
    """Texto da linha de status quando o agente esta trabalhando (ex.: 'Cooking… (12s · esc to interrupt)')."""
    for ln in reversed(lines[-14:]):
        m = _SPINNER.match(ln)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()[:160]
    return ""


def render_conversation(raw: str, cols: int = 120, rows: int = 40) -> tuple[list[dict], str]:
    """Eventos da tela (ordens e respostas ja estruturadas) e a atividade atual, se houver."""
    scr = Screen(cols, rows)
    scr.feed(raw or "")
    lines = [ln[:240] for ln in scr.lines()]
    events = []
    for e in extract_events(lines, partial_top=not scr.history):
        if e["k"] == "ordem":
            events.append({"k": "ordem", "x": " ".join(s.strip() for s in e["lines"])})
        else:
            events.append({"k": "resp", "items": structure_block(e["lines"]), **({"parcial": True} if e.get("parcial") else {})})
    return events, detect_activity(lines)
