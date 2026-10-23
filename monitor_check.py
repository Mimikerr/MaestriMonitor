"""Monitor dos andares.

Spike (2026-10-23): o --pre-run não tem maestri no PATH e o executável
absoluto falha com MAESTRI_PIPE not set em floor status/check. Portanto este
script coleta arquivos e Git; o Monitor consulta status/check via LLM só quando
há anomalia. workspace.json é fonte somente de leitura. Em amostra manual de
30 s, status e lastActiveAt não mudaram; use HEAD e hash do status como sinais
principais e lastActiveAt apenas como auxiliar.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_CONFIG = Path(__file__).with_name("monitor-config.json")
DEFAULT_TEMPLATE = Path(__file__).with_name("painel_template.html")
WORKSPACE_NAME = "Smart-balança"
LEVEL_RANK = {"OK": 0, "AVISO": 1, "CRITICO": 2}


@dataclass(frozen=True)
class Event:
    kind: str
    target: str
    level: str
    detail: str
    action: str

    def line(self) -> str:
        return f"[{self.kind}] {self.target} | {self.detail} | {self.action}"


@dataclass
class MonitorResult:
    exit_code: int
    events: list[Event]
    panel_path: Path
    state_path: Path


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _read_json(path: Path | None) -> Any:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def find_workspace_file(workspaces_dir: Path, workspace_name: str = WORKSPACE_NAME) -> Path | None:
    """Find workspace by payload.name; never persist or assume its generated id."""
    try:
        candidates = sorted(workspaces_dir.glob("*/workspace.json"))
    except OSError:
        return None
    for path in candidates:
        data = _read_json(path)
        payload = data.get("payload", {}) if isinstance(data, dict) else {}
        if isinstance(payload, dict) and payload.get("name") == workspace_name:
            return path
    return None


def parse_roadmap(text: str) -> dict[str, dict[str, Any]]:
    """Parse both the compact monitor format and the current roadmap bullets."""
    items: dict[str, dict[str, Any]] = {}
    pattern = re.compile(
        r"^\s*-\s*\[([x~! ])\]\s*(?:\[[0-9a-fA-F]{4,40}\]\s*)?"
        r"(?:\*\*)?(R\d{2})(?:\s*[—–:-]|\s+)(.*)$"
    )
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        marker, item_id, tail = match.groups()
        marker = marker.lower()
        state = {"x": "done", "~": "reserved", "!": "blocked", " ": "todo"}[marker]
        deps: list[str] = []
        dep_match = re.search(r"(?:·\s*dep\s+|\bdep(?:ende)?:\s*)(.*)$", tail, re.IGNORECASE)
        if dep_match:
            for start, end in re.findall(r"R(\d{2})\s*[–—-]?\s*(?:R(\d{2}))?", dep_match.group(1), re.IGNORECASE):
                if end and int(end) >= int(start):
                    deps.extend(f"R{number:02d}" for number in range(int(start), int(end) + 1))
                else:
                    deps.append(f"R{start}")
        reservation = re.search(r"\((Ground|Dev\s*2|Dev\s*3|[^)]+)\)", tail, re.IGNORECASE)
        reserved_for = reservation.group(1).strip() if reservation and (state == "reserved" or "andar" in reservation.group(1).lower()) else None
        items[item_id] = {
            "id": item_id, "state": state, "title": re.sub(r"\*\*$", "", tail).strip(),
            "deps": list(dict.fromkeys(deps)),
            "reserved": state == "reserved" or reserved_for is not None,
            "reserved_for": reserved_for,
        }
    return items


def propose_items(roadmap: dict[str, dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Choose the first ready item on each configured track, then a fallback."""
    metadata = config.get("items", {})
    occupied: set[str] = set()
    for item_id, row in roadmap.items():
        if row.get("state") in ("working", "reserved"):
            occupied.update(metadata.get(item_id, {}).get("risks", []))
    proposed: set[str] = set()
    result: list[dict[str, Any]] = []
    for floor in config.get("floors", []):
        track = list(floor.get("track", []))
        chosen = None
        outside = False
        for candidate_list, is_outside in ((track, False), (list(roadmap), True)):
            for item_id in candidate_list:
                row = roadmap.get(item_id)
                if not row or row.get("state") != "todo" or row.get("reserved") or item_id in proposed:
                    continue
                if any(roadmap.get(dep, {}).get("state") != "done" for dep in row.get("deps", [])):
                    continue
                risks = set(metadata.get(item_id, {}).get("risks", []))
                if risks & occupied:
                    continue
                chosen = (item_id, row, risks)
                outside = is_outside
                break
            if chosen:
                break
        if chosen:
            item_id, row, risks = chosen
            proposed.add(item_id)
            occupied.update(risks)
            result.append({
                "floor": floor.get("name", ""), "item": item_id, "title": row.get("title", ""),
                "deps": row.get("deps", []), "outside_track": outside,
                "reason": "primeiro item desbloqueado da trilha" if not outside else "trilha esgotada; sugestão fora da trilha",
                "risk": ", ".join(sorted(risks)) or "baixo",
            })
        else:
            result.append({"floor": floor.get("name", ""), "item": None, "deps": [],
                           "outside_track": False, "reason": "nenhum item desbloqueado", "risk": "baixo"})
    return result


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _account_rows(status: Any, now: datetime, warning: float = 75, critical: float = 90,
                  watched_accounts: list[str] | None = None) -> tuple[list[dict[str, Any]], list[Event]]:
    rows: list[dict[str, Any]] = []
    events: list[Event] = []
    providers = status.get("providers", []) if isinstance(status, dict) else []
    watched = {str(value).strip().casefold() for value in (watched_accounts or [])}
    weekly: list[tuple[str, float]] = []
    for provider in providers if isinstance(providers, list) else []:
        if not isinstance(provider, dict):
            continue
        name = str(provider.get("name") or provider.get("id") or "Conta")
        identifiers = {name.strip().casefold(), str(provider.get("id") or "").strip().casefold()}
        is_watched = bool(identifiers & watched)
        plan = str(provider.get("plan") or "")
        account = f"{name} ({plan})" if plan else name
        provider_windows = []
        meters = provider.get("meters", [])
        for meter in meters if isinstance(meters, list) else []:
            if not isinstance(meter, dict):
                continue
            windows = meter.get("windows", [])
            for window in windows if isinstance(windows, list) else []:
                if not isinstance(window, dict):
                    continue
                percent = _number(window.get("usedPercent"))
                ring = str(window.get("ring") or window.get("id") or "janela")
                reset = str(window.get("resetsAt") or "indisponível")
                duration = _number(window.get("durationSeconds"))
                reset_time = _parse_time(window.get("resetsAt"))
                projection = None
                if reset_time and duration > 0:
                    elapsed = (now - (reset_time - timedelta(seconds=duration))).total_seconds()
                    elapsed_fraction = min(1.0, max(0.0, elapsed / duration))
                    if elapsed_fraction > 0:
                        projection = percent / elapsed_fraction
                provider_windows.append({"ring": ring, "used": percent, "reset": reset,
                                         "projection": projection, "duration": duration})
                target = f"{account} {ring}"
                if is_watched and percent >= critical:
                    events.append(Event("conta_critica", target, "CRITICO", f"uso em {percent:g}%", "avaliar conta com menor uso semanal"))
                elif is_watched and percent >= warning:
                    events.append(Event("conta_aviso", target, "AVISO", f"uso em {percent:g}%", "avaliar conta com menor uso semanal"))
                if is_watched and projection is not None and projection > 100 and percent >= 40:
                    events.append(Event("ritmo", target, "AVISO", f"ritmo projeta {projection:.0f}% antes do reset", "avaliar conta com menor uso semanal"))
                if is_watched and ("week" in ring.lower() or "semana" in ring.lower()):
                    weekly.append((account, percent))
                level = "CRITICO" if percent >= critical else "AVISO" if percent >= warning else "OK"
                provider_windows[-1]["level"] = level
        rows.append({"account": account, "watched": is_watched, "windows": provider_windows})
    lowest_weekly = min(weekly, key=lambda row: row[1]) if weekly else None
    recommendation = (f"avaliar troca; menor uso semanal: {lowest_weekly[0]} ({lowest_weekly[1]:g}%)"
                      if lowest_weekly else "uso semanal indisponível")
    for row in rows:
        row["recommendation"] = recommendation if row["watched"] else f"conta informativa; {recommendation}"
    events = [Event(event.kind, event.target, event.level, event.detail,
                    f"{event.action}; {recommendation}") for event in events]
    return rows, events


def _workspace_payload(workspace_path: Path | None, workspace_name: str) -> tuple[dict[str, Any] | None, str | None]:
    data = _read_json(workspace_path)
    if not isinstance(data, dict):
        return None, "workspace indisponível"
    payload = data.get("payload")
    if not isinstance(payload, dict) or payload.get("name") != workspace_name:
        return None, "workspace indisponível ou nome divergente"
    return payload, None


def _terminal_nodes(nodes: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for node in nodes if isinstance(nodes, list) else []:
        content = node.get("content", {}) if isinstance(node, dict) else {}
        terminal = content.get("terminal", {}) if isinstance(content, dict) else {}
        data = terminal.get("_0") if isinstance(terminal, dict) else None
        if isinstance(data, dict) and data.get("name"):
            result[str(data["name"])] = data
    return result


def _worktree_paths(repo: Path, runner: Callable[..., Any]) -> dict[str, Path]:
    try:
        result = runner(["git", "worktree", "list", "--porcelain"], cwd=str(repo),
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        if result.returncode != 0:
            return {}
        current_path = None
        mapping: dict[str, Path] = {}
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current_path = Path(line[9:])
            elif line.startswith("branch refs/heads/") and current_path is not None:
                mapping[line.removeprefix("branch refs/heads/")] = current_path
        return mapping
    except (OSError, subprocess.SubprocessError):
        return {}


def _branch_head(branch: str, repo: Path, worktrees: dict[str, Path], runner: Callable[..., Any]) -> str | None:
    path = worktrees.get(branch)
    if path is None and branch == "dev":
        path = repo
    if path is None:
        return None
    try:
        result = runner(["git", "-C", str(path), "rev-parse", "HEAD"], cwd=str(repo),
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _status_signature(status: Any) -> str | None:
    if not isinstance(status, dict):
        return None
    raw = json.dumps({"state": status.get("state"), "text": status.get("text"),
                      "progress": status.get("progress")}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _git_status_hash(branch: str, repo: Path, worktrees: dict[str, Path], runner: Callable[..., Any]) -> str | None:
    path = worktrees.get(branch)
    if path is None and branch == "dev":
        path = repo
    if path is None:
        return None
    try:
        result = runner(["git", "-C", str(path), "status", "--short", "--branch"], cwd=str(repo),
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        return hashlib.sha256(result.stdout.encode("utf-8")).hexdigest() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _floor_reserved(name: str, configured: dict[str, Any], roadmap: dict[str, dict[str, Any]]) -> bool:
    aliases = {name.casefold()}
    if name == "Ground":
        aliases.update({"térreo", "terreo"})
    elif name == "Dev 2":
        aliases.update({"dev2", "andar dev-home"})
    elif name == "Dev 3":
        aliases.update({"dev3", "andar dev-3"})
    track = set(configured.get("track", []))
    return any(item_id in track and item.get("reserved")
               and str(item.get("reserved_for") or "").strip().casefold() in aliases
               for item_id, item in roadmap.items())


def _floor_views(payload: dict[str, Any], config: dict[str, Any], repo: Path,
                 old_state: dict[str, Any], now: datetime,
                 runner: Callable[..., Any], roadmap: dict[str, dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[Event], dict[str, Any]]:
    workspace_floors = {str(floor.get("name")): floor for floor in payload.get("floors", [])
                        if isinstance(floor, dict) and floor.get("name")}
    worktrees = _worktree_paths(repo, runner)
    old_floors = old_state.get("floors", {}) if isinstance(old_state.get("floors"), dict) else {}
    views: list[dict[str, Any]] = []
    events: list[Event] = []
    next_floors: dict[str, Any] = {}
    threshold = int(config.get("thresholds", {}).get("stalled_minutes", 30))
    for configured in config.get("floors", []):
        name = str(configured.get("name", ""))
        workspace_floor = workspace_floors.get(name, {})
        status = workspace_floor.get("status") if isinstance(workspace_floor, dict) else None
        status_state = str(status.get("state", "")) if isinstance(status, dict) else ""
        status_text = str(status.get("text", "")) if isinstance(status, dict) else ""
        branch = str(workspace_floor.get("branchName") or configured.get("branch") or "") if isinstance(workspace_floor, dict) else str(configured.get("branch") or "")
        head = _branch_head(branch, repo, worktrees, runner) if branch else None
        git_status = _git_status_hash(branch, repo, worktrees, runner) if not status_state and branch else None
        nodes = payload.get("nodes", []) if name == "Ground" else workspace_floor.get("nodes", [])
        terminals = _terminal_nodes(nodes)
        terminal = next((node for node in terminals.values() if node.get("isManager")), None)
        terminal = terminal or next(iter(terminals.values()), {})
        last_active = terminal.get("lastActiveAt")
        signals = {"status": _status_signature(status), "head": head, "git_status": git_status,
                   "lastActiveAt": last_active}
        previous = old_floors.get(name, {}) if isinstance(old_floors, dict) else {}
        previous_signals = previous.get("signals") if isinstance(previous, dict) else None
        last_change = _parse_time(previous.get("last_change")) if isinstance(previous, dict) else None
        if not isinstance(previous_signals, dict) or previous_signals != signals or last_change is None:
            last_change = now
        elapsed_minutes = max(0, int((now - last_change).total_seconds() // 60))
        reserved = _floor_reserved(name, configured, roadmap or {})
        has_activity_signals = any((head, git_status, last_active))
        if status_state == "review":
            marker_names = {"Ground": "Ground.avisado", "Dev 2": "Dev2.avisado", "Dev 3": "Dev3.avisado"}
            marker_name = marker_names.get(name)
            marker_dir = Path(config["markers_dir"]) if config.get("markers_dir") else None
            marker = marker_dir / marker_name if marker_dir and marker_name else None
            if not marker or not marker.exists():
                events.append(Event("review", name, "AVISO", status_text or "status em review", "revisar o relatório do andar"))
        elif status_state == "blocked" and elapsed_minutes >= threshold:
            events.append(Event("travado", name, "CRITICO", f"blocked há {elapsed_minutes} min: {status_text}", "consultar o orquestrador"))
        elif status_state == "working" and elapsed_minutes >= threshold:
            events.append(Event("parado", name, "AVISO", f"sem sinal novo há {elapsed_minutes} min", "consultar o orquestrador"))
        if not status_state and has_activity_signals and elapsed_minutes >= threshold:
            events.append(Event("parado", name, "AVISO", f"sem sinal novo há {elapsed_minutes} min", "consultar o orquestrador"))
        if status_state:
            display_state = "ocioso" if status_state == "done" else status_state
        elif reserved or (has_activity_signals and elapsed_minutes < threshold):
            display_state = "working"
        elif has_activity_signals:
            display_state = "parado"
        else:
            display_state = "ocioso"
        views.append({"floor": name, "branch": branch, "head": head or "indisponível",
                      "state": display_state, "text": status_text or "sem status",
                      "terminal": terminal.get("name", "indisponível"),
                      "terminal_status": terminal.get("status", "indisponível"),
                      "last_active": last_active or "indisponível", "idle_minutes": elapsed_minutes,
                      "alert": next((event.kind for event in reversed(events) if event.target == name), "")})
        next_floors[name] = {"signals": signals, "last_change": last_change.isoformat()}
    return views, events, next_floors


def _event_key(event: Event) -> str:
    return "|".join((event.target, event.kind))


def _new_events(candidates: list[Event], state: dict[str, Any], now: datetime,
                reminder_minutes: int) -> tuple[list[Event], dict[str, Any]]:
    records = state.get("events", {}) if isinstance(state.get("events"), dict) else {}
    sent: list[Event] = []
    live_keys: set[str] = set()
    for event in candidates:
        key = _event_key(event)
        live_keys.add(key)
        record = records.get(key, {}) if isinstance(records.get(key), dict) else {}
        previous_level = str(record.get("level", "OK"))
        last_sent = _parse_time(record.get("last_sent"))
        rank_up = LEVEL_RANK.get(event.level, 0) > LEVEL_RANK.get(previous_level, 0)
        due = last_sent is None or rank_up or (now - last_sent) >= timedelta(minutes=reminder_minutes)
        if due:
            sent.append(event)
            records[key] = {"level": event.level, "last_sent": now.isoformat(), "detail": event.detail}
    for key in list(records):
        if key not in live_keys:
            records.pop(key, None)
    state["events"] = records
    return sent, state


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _render(template_path: Path, now: datetime, data_age: str, floors: list[dict[str, Any]],
            roadmap: dict[str, dict[str, Any]] | None, proposals: list[dict[str, Any]],
            accounts: list[dict[str, Any]], unavailable: list[str], events: list[Event]) -> str:
    template = template_path.read_text(encoding="utf-8")
    def rows(items: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
        output = []
        for item in items:
            output.append("<tr>" + "".join(f"<td>{_escape(item.get(key, ''))}</td>" for key, _ in columns) + "</tr>")
        return "\n".join(output) or f'<tr><td colspan="{len(columns)}">Sem dados</td></tr>'
    floor_html = rows(floors, [("floor", "Andar"), ("state", "Estado"), ("text", "Item/status atual"),
                               ("terminal", "Terminal"), ("terminal_status", "Terminal ativo"),
                               ("last_active", "Última atividade"), ("idle_minutes", "Min sem mudança"),
                               ("alert", "Alerta"), ("head", "HEAD")])
    demand_rows = []
    if roadmap is not None:
        demand_rows = [{"item": item_id, "state": item["state"], "title": item["title"],
                        "deps": ", ".join(item["deps"])} for item_id, item in roadmap.items()]
    demand_html = rows(demand_rows, [("item", "Item"), ("state", "Estado"), ("title", "Demanda"), ("deps", "Dependências")])
    proposal_html = rows(proposals, [("floor", "Andar"), ("item", "Item"), ("deps", "Dependências"),
                                    ("reason", "Motivo"), ("risk", "Risco")])
    account_rows = []
    for account in accounts:
        for window in account["windows"]:
            amount = window["used"]
            level = window["level"]
            account_rows.append({"account": account["account"], "ring": window["ring"], "used": amount,
                                 "level": level, "reset": window["reset"],
                                 "projection": f"{window['projection']:.0f}%" if window["projection"] is not None else "indisponível",
                                 "recommendation": account["recommendation"]})
    account_html = "\n".join(
        f'<tr class="{_escape(row["level"])}"><td>{_escape(row["account"])}</td>'
        f'<td>{_escape(row["ring"])}</td><td><span class="bar" aria-label="{row["used"]:g}%">'
        f'<i style="width:{max(0, min(100, row["used"])):g}%"></i></span> {row["used"]:g}%</td>'
        f'<td>{_escape(row["level"])}</td><td>{_escape(row["reset"])}</td>'
        f'<td>{_escape(row["projection"])}</td><td>{_escape(row["recommendation"])}</td></tr>'
        for row in account_rows
    ) or '<tr><td colspan="7">Sem dados</td></tr>'
    availability = "; ".join(unavailable) if unavailable else "todas as fontes principais disponíveis"
    event_html = rows([{"kind": event.kind, "target": event.target, "detail": event.detail} for event in events],
                      [("kind", "Tipo"), ("target", "Alvo"), ("detail", "Detalhe")])
    counts = roadmap or {}
    replacements = {
        "{{UPDATED_AT}}": _escape(now.isoformat()), "{{DATA_AGE}}": _escape(data_age),
        "{{SOURCE_STATUS}}": _escape(availability), "{{FLOOR_ROWS}}": floor_html,
        "{{DEMAND_ROWS}}": demand_html, "{{PROPOSAL_ROWS}}": proposal_html,
        "{{ACCOUNT_ROWS}}": account_html, "{{EVENT_ROWS}}": event_html,
        "{{ROADMAP_COUNTS}}": _escape(
            f"{sum(x['state']=='done' for x in counts.values())} feitos · "
            f"{sum(x['state'] in ('working', 'reserved') for x in counts.values())} em andamento · "
            f"{sum(x['state']=='todo' for x in counts.values())} a fazer · "
            f"{sum(x['state']=='blocked' for x in counts.values())} bloqueados"),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def _paths(paths: dict[str, Path] | None) -> dict[str, Path | None]:
    home = Path.home()
    repo = Path(__file__).resolve().parents[2]
    runtime = home / "StudioProjects" / ".maestri" / "monitor"
    workspace_dir = home / ".maestri" / "workspaces"
    marker_dir = home / "StudioProjects" / ".maestri" / "routine-andares"
    result: dict[str, Path | None] = {
        "repo": repo, "runtime": runtime,
        "usage": home / ".maestri" / "usage" / "providers" / ".status.json",
        "workspace_dir": workspace_dir, "workspace": find_workspace_file(workspace_dir),
        "markers_dir": marker_dir, "config": DEFAULT_CONFIG, "template": DEFAULT_TEMPLATE,
    }
    if paths:
        for key, value in paths.items():
            if value is None:
                result[key] = None
            else:
                result[key] = Path(value)
    if paths and "workspace" not in paths:
        result["workspace"] = find_workspace_file(result["workspace_dir"] or workspace_dir)
    return result


def _age_label(paths: dict[str, Path | None], now: datetime) -> str:
    parts = []
    for label, key in (("workspace", "workspace"), ("contas", "usage")):
        path = paths.get(key)
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if path else None
        except OSError:
            modified = None
        if modified is None:
            parts.append(f"{label}: indisponível")
        else:
            minutes = max(0, int((now - modified).total_seconds() // 60))
            parts.append(f"{label}: {minutes} min")
    parts.append("roadmap: atualizado neste ciclo")
    return " · ".join(parts)


def run_check(now: datetime | None = None, paths: dict[str, Path] | None = None,
              runner: Callable[..., Any] | None = None) -> MonitorResult:
    now = _utc(now)
    resolved = _paths(paths)
    runner = runner or subprocess.run
    runtime = resolved["runtime"]
    repo = resolved["repo"]
    state_path = runtime / "state.json"
    panel_path = runtime / "painel.html"
    config = _read_json(resolved["config"]) or {}
    config.setdefault("workspace_name", WORKSPACE_NAME)
    config.setdefault("markers_dir", str(resolved["markers_dir"]))
    old_state = _read_json(state_path)
    if not isinstance(old_state, dict):
        old_state = {"floors": {}, "events": {}}
    unavailable: list[str] = []
    payload, workspace_error = _workspace_payload(resolved.get("workspace"), config["workspace_name"])
    if workspace_error:
        unavailable.append(workspace_error)
        payload = {"floors": [], "nodes": []}
    try:
        runner(["git", "fetch", "origin", "dev"], cwd=str(repo), capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
    roadmap = None
    try:
        result = runner(["git", "show", "origin/dev:docs/roadmap.md"], cwd=str(repo),
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if result.returncode == 0 and isinstance(result.stdout, str):
            roadmap = parse_roadmap(result.stdout)
        else:
            unavailable.append("roadmap indisponível")
    except (OSError, subprocess.SubprocessError):
        unavailable.append("roadmap indisponível")
    proposals = propose_items(roadmap or {}, config)
    floor_views, floor_events, next_floors = _floor_views(payload, config, repo, old_state, now, runner, roadmap or {})
    account_status = _read_json(resolved["usage"])
    if account_status is None:
        unavailable.append("contas indisponíveis")
        account_status = {"providers": []}
    thresholds = config.get("thresholds", {})
    account_rows, account_events = _account_rows(account_status, now,
                                                 float(thresholds.get("warning", 75)),
                                                 float(thresholds.get("critical", 90)),
                                                 list(config.get("contas_vigiadas", [])))
    reminder = int(config.get("thresholds", {}).get("reminder_minutes", 120))
    sent_events, next_state = _new_events(floor_events + account_events, old_state, now, reminder)
    next_state.update({"schema_version": 1, "updated_at": now.isoformat(), "floors": next_floors})
    _write_json(state_path, next_state)
    try:
        rendered = _render(resolved["template"], now, _age_label(resolved, now), floor_views, roadmap,
                           proposals, account_rows, unavailable, floor_events + account_events)
    except (OSError, UnicodeError):
        rendered = _render(DEFAULT_TEMPLATE, now, _age_label(resolved, now), floor_views, roadmap,
                           proposals, account_rows, unavailable + ["template indisponível"],
                           floor_events + account_events)
    panel_path.parent.mkdir(parents=True, exist_ok=True)
    panel_path.write_text(rendered, encoding="utf-8")
    return MonitorResult(0 if sent_events else 1, sent_events, panel_path, state_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vigia os andares, roadmap e limites das contas")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    result = run_check(paths={"config": args.config})
    for event in result.events:
        print(event.line())
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
