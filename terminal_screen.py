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

    # --- operacoes basicas ---
    def _blank(self) -> list[str]:
        return [" "] * self.cols

    def _scroll_up(self, n: int = 1) -> None:
        for _ in range(n):
            gone = self.grid.pop(self.top)
            if self.top == 0:
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
            return  # modos (?25h, ?1049h...) nao mudam o texto
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
