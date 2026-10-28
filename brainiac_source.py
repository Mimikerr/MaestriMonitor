"""Fonte de dados do painel via Brainiac (somente leitura, sem gastar token).

Usa o CLI do Brainiac (`brainiac call <metodo> '<json>'`), a mesma API do app:
agents.list, workspaces.list, terminals.scrollback e usage.getState. Nada aqui
inicia agente, envia texto a terminal ou altera repositorio.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "projetos.json"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]")
STEP = re.compile(r"^\s*[-*]\s*\[( |x|X|~|>)\]\s*(.+?)\s*$")


def default_brainiac_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / "Brainiac" / "resources" / "app"


class BrainiacError(Exception):
    pass


def call(method: str, params: dict[str, Any] | None = None, brainiac_dir: Path | None = None,
         runner: Callable[..., Any] = subprocess.run, timeout: int = 20) -> Any:
    app = brainiac_dir or default_brainiac_dir()
    node, cli = app / "runtime" / "node.exe", app / "cli" / "src" / "main.ts"
    env = {k: v for k, v in os.environ.items() if k != "ELECTRON_RUN_AS_NODE"}
    try:
        done = runner([str(node), str(cli), "call", method, json.dumps(params or {})],
                      capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrainiacError(f"Brainiac indisponivel: {exc}") from exc
    if done.returncode != 0:
        raise BrainiacError((done.stderr or done.stdout or "erro desconhecido").strip()[:300])
    try:
        return json.loads(done.stdout)
    except ValueError as exc:
        raise BrainiacError("Resposta invalida do Brainiac") from exc


def clean_terminal(raw: str, keep: int = 8) -> list[str]:
    """Ultimas linhas legiveis de um scrollback cru (remove sequencias ANSI)."""
    text = ANSI.sub("", raw or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[\x00-\x08\x0b-\x1f]", "", ln).rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln.strip()]
    return [ln[:200] for ln in lines[-keep:]]


def parse_plan(text: str) -> dict[str, Any]:
    """Checklist em Markdown: [x] feito, [~] em andamento, [ ] proximo.
    Sem [~], o primeiro [ ] e o passo atual."""
    steps: list[dict[str, str]] = []
    for line in (text or "").splitlines():
        m = STEP.match(line)
        if m:
            mark = m.group(1).lower()
            state = "done" if mark == "x" else "doing" if mark in "~>" else "todo"
            steps.append({"state": state, "text": m.group(2)})
    if steps and not any(s["state"] == "doing" for s in steps):
        for s in steps:
            if s["state"] == "todo":
                s["state"] = "doing"
                break
    done = sum(1 for s in steps if s["state"] == "done")
    return {"steps": steps, "done": done, "total": len(steps)}


def _git(repo: Path, args: list[str], runner: Callable[..., Any]) -> list[str]:
    try:
        done = runner(["git", "-C", str(repo), *args], capture_output=True, text=True,
                      encoding="utf-8", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    return [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]


def branch_report(repo: Path, integration: str, release: str,
                  runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Classifica branches remotas ja buscadas (sem git fetch)."""
    skip = {f"origin/{integration}", f"origin/{release}", "origin/HEAD", "origin"}
    fmt = "--format=%(refname:short)"
    allb = [b for b in _git(repo, ["branch", "-r", fmt], runner) if b not in skip and not b.endswith("/HEAD")]
    in_release = set(_git(repo, ["branch", "-r", "--merged", f"origin/{release}", fmt], runner))
    in_integration = set(_git(repo, ["branch", "-r", "--merged", f"origin/{integration}", fmt], runner))
    ahead = _git(repo, ["rev-list", "--count", f"origin/{release}..origin/{integration}"], runner)
    return {
        "aguardando_release": sorted(b for b in allb if b in in_integration and b not in in_release),
        "em_andamento": sorted(b for b in allb if b not in in_integration),
        "limpar": sorted(b for b in allb if b in in_release),
        "commits_na_frente": int(ahead[0]) if ahead and ahead[0].isdigit() else 0,
    }


def _relative(value: str | None, now: datetime) -> str:
    if not value:
        return ""
    try:
        then = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    sec = int((now - then).total_seconds())
    if sec < 90:
        return "agora"
    if sec < 3600:
        return f"ha {sec // 60} min"
    if sec < 86400:
        return f"ha {sec // 3600} h"
    return f"ha {sec // 86400} d"


def usage_rows(state: Any) -> list[dict[str, Any]]:
    rows = []
    for acc in (state or {}).get("accounts", []):
        snap = acc.get("snapshot") or {}
        windows = [{"label": f"{m.get('label', '')} · {w.get('label', '')}".strip(" ·"),
                    "percent": round(float(w.get("usedFraction") or 0) * 100),
                    "resets_at": w.get("resetsAt")}
                   for m in snap.get("meters", []) for w in m.get("windows", [])]
        if windows:
            rows.append({"provider": acc.get("provider", ""), "label": acc.get("label", ""),
                         "phase": acc.get("phase", ""), "windows": windows})
    return rows


def build_snapshot(config: dict[str, Any], brainiac_dir: Path | None = None,
                   runner: Callable[..., Any] = subprocess.run, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    snap: dict[str, Any] = {"generated_at": now.isoformat(), "ok": True, "error": None,
                            "projects": [], "usage": []}
    try:
        workspaces = call("workspaces.list", {}, brainiac_dir, runner)
        agents = call("agents.list", {}, brainiac_dir, runner)
    except BrainiacError as exc:
        snap.update(ok=False, error=str(exc))
        return snap
    try:
        snap["usage"] = usage_rows(call("usage.getState", {}, brainiac_dir, runner))
    except BrainiacError:
        snap["usage"] = []
    by_name = {w["name"]: w for w in workspaces}
    for proj in config.get("projetos", []):
        ws = by_name.get(proj.get("workspace", ""))
        item: dict[str, Any] = {"apelido": proj.get("apelido", ""), "nome": proj.get("workspace", ""),
                                "cor": proj.get("cor", "#1F2A44"), "encontrado": ws is not None, "agentes": [], "plano": None, "branches": None}
        if ws:
            for ag in (a for a in agents if a.get("workspaceId") == ws["id"]):
                cfg = ag.get("config") or {}
                last = (cfg.get("metadata") or {}).get("lastTurnEndedAt")
                is_maestro = ag.get("name") == proj.get("maestro")
                row = {"nome": ag.get("name", ""), "provider": ag.get("provider", ""),
                       "status": ag.get("status", ""), "erro": ag.get("error"), "maestro": is_maestro,
                       "ultimo_turno": _relative(last, now), "terminal": []}
                if is_maestro and ag.get("terminalId") and ag.get("status") == "running":
                    try:
                        sb = call("terminals.scrollback", {"id": ag["terminalId"]}, brainiac_dir, runner)
                        row["terminal"] = clean_terminal(sb.get("data", ""))
                    except BrainiacError:
                        pass
                item["agentes"].append(row)
            item["agentes"].sort(key=lambda r: (not r["maestro"], r["nome"]))
        root = Path(proj["pasta"]) if proj.get("pasta") else (Path(ws["rootPath"]) if ws else None)
        plan_file = proj.get("plano")
        if root and plan_file and (root / plan_file).is_file():
            item["plano"] = parse_plan((root / plan_file).read_text(encoding="utf-8", errors="replace"))
        if root and proj.get("branch_integracao") and proj.get("branch_release") and (root / ".git").exists():
            item["branches"] = branch_report(root, proj["branch_integracao"], proj["branch_release"], runner)
        snap["projects"].append(item)
    return snap


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"projetos": []}
