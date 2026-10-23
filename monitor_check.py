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
        status_signature = _status_signature(status)
        previous_status_signature = previous_signals.get("status") if isinstance(previous_signals, dict) else None
        status_last_change = _parse_time(previous.get("status_last_change")) if isinstance(previous, dict) else None
        if status_signature != previous_status_signature or status_last_change is None:
            status_last_change = now
        status_elapsed_minutes = max(0, int((now - status_last_change).total_seconds() // 60))
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
        elif status_state not in ("", "working", "review", "done", "blocked") and status_text and status_elapsed_minutes >= threshold:
            events.append(Event("travado", name, "CRITICO",
                                f"status {status_state} sem mudança há {status_elapsed_minutes} min: {status_text}",
                                "consultar o orquestrador"))
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
        next_floors[name] = {"signals": signals, "last_change": last_change.isoformat(),
                             "status_last_change": status_last_change.isoformat()}
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


FLOOR_STATES = {"working": ("Trabalhando", "ok"), "ocioso": ("Ocioso", "neutral"), "parado": ("Parado", "warn"),
                "blocked": ("Bloqueado", "crit"), "review": ("Em review", "info")}
FLOOR_ALERTS = {"parado": ("Parado", "warn"), "travado": ("Travado", "crit"), "review": ("Em review", "info")}
TERMINAL_STATES = {"stopped": "parado", "restored": "restaurado", "running": "rodando", "idle": "ocioso"}
EVENT_LABELS = {"parado": "Parado", "travado": "Travado", "review": "Review", "conta_aviso": "Conta em aviso",
                "conta_critica": "Conta crítica", "ritmo": "Ritmo"}
LEVEL_TONES = {"OK": "ok", "AVISO": "warn", "CRITICO": "crit"}
DEMAND_STATES = {"reserved": ("Reservado", "info", "andamento"), "working": ("Em andamento", "info", "andamento"),
                 "todo": ("A fazer", "neutral", "todo"), "blocked": ("Bloqueado", "crit", "blocked"),
                 "done": ("Feito", "ok", "done")}
DEMAND_FILTERS = (("todos", "Todos", ""), ("andamento", "Em andamento", "info"), ("todo", "A fazer", "neutral"),
                  ("blocked", "Bloqueados", "crit"), ("done", "Feitos", "ok"))
RING_LABELS = {"session": "Sessão", "weekly": "Semanal", "special": "Especial"}
ALERT_ICON = ('<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 2 21h20L12 3z"/>'
              '<path d="M12 10v5"/><path d="M12 18h.01"/></svg>')
CHECK_ICON = ('<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
              'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>')


def _relative(value: Any, now: datetime) -> str:
    moment = _parse_time(value)
    if moment is None:
        return str(value)
    seconds = int((moment - now).total_seconds())
    future = seconds > 0
    minutes = abs(seconds) // 60
    if minutes == 0:
        return "agora"
    if minutes < 60:
        text = f"{minutes} min"
    elif minutes < 1440:
        hours, rest = divmod(minutes, 60)
        text = f"{hours} h {rest} min" if future and rest else f"{hours} h"
    else:
        days, rest = divmod(minutes, 1440)
        if future:
            text = f"{days} d {rest // 60} h" if rest // 60 else f"{days} d"
        else:
            text = "1 dia" if days == 1 else f"{days} dias"
    return ("em " if future else "há ") + text


def _badge(text: str, tone: str, strong: bool = False) -> str:
    return f'<span class="badge {tone}{" strong" if strong else ""}">{_escape(text)}</span>'


def _chips(deps: list[str]) -> str:
    if not deps:
        return '<span class="muted">—</span>'
    return '<div class="chips">' + "".join(f'<span class="chip mono">{_escape(dep)}</span>' for dep in deps) + "</div>"


def _event_target(target: str) -> str:
    head, _, ring = target.rpartition(" ")
    return f"{head} · {RING_LABELS[ring.lower()].lower()}" if head and ring.lower() in RING_LABELS else target


def _demand_title(title: str) -> str:
    return title.split("**", 1)[0].strip().replace("`", "")


def _floor_card(floor: dict[str, Any], events: list[Event], now: datetime) -> str:
    name = str(floor.get("floor", ""))
    state = str(floor.get("state", ""))
    label, tone = FLOOR_STATES.get(state, (state.capitalize() or "Indisponível", "warn"))
    event = next((item for item in events if item.target == name and item.kind in FLOOR_ALERTS), None)
    if event:
        label, tone = FLOOR_ALERTS[event.kind]
    card_tone = tone if tone in ("warn", "crit") else ""
    terminal_status = str(floor.get("terminal_status", ""))
    terminal_label = TERMINAL_STATES.get(terminal_status, terminal_status)
    head = str(floor.get("head", ""))
    short_head = head[:7] if re.fullmatch(r"[0-9a-f]{7,40}", head) else head
    last_active = floor.get("last_active", "")
    callout = (f'<div class="callout {tone}">{ALERT_ICON}{_escape(event.detail)}</div>' if event else "")
    return (
        f'<article class="card floor {card_tone}" data-floor="{_escape(name)}" data-state="{_escape(state)}">'
        f'<div class="card-head"><div><h2>{_escape(name)}</h2><span class="sub">branch {_escape(floor.get("branch") or "—")}</span></div>'
        f'{_badge(label, tone, strong=True)}</div>'
        f'<div><span class="label">Item / status atual</span><p class="clamp" title="{_escape(floor.get("text", ""))}">'
        f'{_escape(floor.get("text", ""))}</p></div>{callout}'
        '<dl class="facts">'
        f'<div><dt>Terminal</dt><dd class="term" title="{_escape(floor.get("terminal", ""))}">'
        f'<span class="dot{"" if terminal_status in ("restored", "running") else " off"}"></span>{_escape(floor.get("terminal", ""))}</dd>'
        f'<dd class="sub">terminal {_escape(terminal_label)}</dd></div>'
        f'<div><dt>Sem mudança</dt><dd class="big {card_tone}">{_escape(floor.get("idle_minutes", 0))} min</dd></div>'
        f'<div><dt>Última atividade do terminal</dt><dd title="{_escape(last_active)}">{_escape(_relative(last_active, now))}</dd></div>'
        f'<div><dt>HEAD</dt><dd class="mono" title="{_escape(head)}">{_escape(short_head)}</dd></div>'
        "</dl></article>"
    )


def _account_card(account: dict[str, Any], now: datetime) -> str:
    worst = "ok"
    meters = []
    for window in account.get("windows", []):
        used = max(0.0, min(100.0, _number(window.get("used"))))
        tone = LEVEL_TONES.get(str(window.get("level")), "ok")
        projection = window.get("projection")
        hot = projection is not None and projection > 100 and used >= 40
        if tone == "crit":
            worst = "crit"
        elif (tone == "warn" or hot) and worst != "crit":
            worst = "warn"
        ring = str(window.get("ring", ""))
        name = RING_LABELS.get(ring.lower(), ring)
        rhythm = f"ritmo projeta {projection:.0f}%" if projection is not None else "ritmo indisponível"
        meters.append(
            f'<div class="meter {tone}"><div class="meter-head"><span>{_escape(name)}</span>'
            f'<strong class="{tone}">{used:.0f}%</strong></div>'
            f'<div class="track" role="img" aria-label="{_escape(name)}: {used:.0f}% usado"><i style="width:{used:.1f}%"></i></div>'
            f'<div class="meter-foot"><span title="{_escape(window.get("reset", ""))}">reset {_escape(_relative(window.get("reset"), now))}</span>'
            f'<span class="{"hot" if hot else ""}">{_escape(rhythm)}</span></div></div>'
        )
    level = {"crit": "Crítico", "warn": "Aviso", "ok": "OK"}[worst]
    sub = "vigiada" if account.get("watched") else "informativa · não dispara troca"
    return (f'<article class="card {worst if worst != "ok" else ""}"><div class="card-head"><div>'
            f'<h2>{_escape(account.get("account", ""))}</h2><span class="sub">{sub}</span></div>'
            f'{_badge(level, worst)}</div>{"".join(meters) or "<p class=muted>Sem janelas de uso.</p>"}</article>')


def _render(template_path: Path, now: datetime, sources: list[tuple[str, str, bool]], floors: list[dict[str, Any]],
            roadmap: dict[str, dict[str, Any]] | None, proposals: list[dict[str, Any]],
            accounts: list[dict[str, Any]], unavailable: list[str], events: list[Event]) -> str:
    template = template_path.read_text(encoding="utf-8")
    source_html = "".join(
        f'<span class="source{"" if ok else " down"}" title="{"fonte disponível" if ok else "fonte indisponível neste ciclo"}">'
        f'<span class="dot"></span>{_escape(label)} · {_escape(age)}</span>' for label, age, ok in sources)
    source_html += "".join(f'<span class="source down"><span class="dot"></span>{_escape(item)}</span>'
                           for item in unavailable if item == "template indisponível")
    ordered = sorted(events, key=lambda item: -LEVEL_RANK.get(item.level, 0))
    alert_html = "".join(
        f'<div class="alert"><span class="icon {LEVEL_TONES.get(event.level, "warn")}">{ALERT_ICON}</span>'
        f'{_badge(EVENT_LABELS.get(event.kind, event.kind), LEVEL_TONES.get(event.level, "warn"))}'
        f'<strong>{_escape(_event_target(event.target))}</strong><span class="detail">{_escape(event.detail)}</span></div>'
        for event in ordered
    ) or (f'<div class="alert none"><span class="icon ok">{CHECK_ICON}</span>Nenhum alerta ativo. O Monitor avisa quando '
          'um andar para, trava ou uma conta passa de 75%.</div>')
    floor_html = "".join(_floor_card(floor, events, now) for floor in floors) or '<div class="empty">Sem andares configurados.</div>'

    rows = roadmap or {}
    groups: dict[str, int] = {"todos": len(rows)}
    demand_rows = []
    for item_id, item in rows.items():
        label, tone, group = DEMAND_STATES.get(item["state"], (item["state"], "neutral", "todo"))
        groups[group] = groups.get(group, 0) + 1
        title = item.get("title", "")
        floor_match = re.search(r"Em andamento \(([^)]+)\)", title)
        floor_note = f'<span class="sub">{_escape(floor_match.group(1))}</span>' if floor_match and group == "andamento" else ""
        block = re.search(r"Bloqueio:\s*(.+?)\s*(?:Em andamento \([^)]*\)\.?)?$", title)
        note = (f'<div class="note">Bloqueio: {_escape(block.group(1))}</div>'
                if block and item["state"] == "blocked" and block.group(1).strip() not in ("—", "-") else "")
        demand_rows.append(
            f'<tr data-group="{group}"><td class="id mono">{_escape(item_id)}</td><td>{_badge(label, tone)}{floor_note}</td>'
            f'<td><div title="{_escape(title)}">{_escape(_demand_title(title))}</div>{note}</td><td>{_chips(item.get("deps", []))}</td></tr>')
    demand_html = "".join(demand_rows) or (
        '<tr><td colspan="4" class="muted">Roadmap indisponível neste ciclo.</td></tr>' if roadmap is None
        else '<tr><td colspan="4" class="muted">Sem itens no roadmap.</td></tr>')
    filter_html = "".join(
        f'<button type="button" class="{tone}" data-filter="{key}" aria-pressed="{"true" if key == "todos" else "false"}">'
        f'<strong>{groups.get(key, 0)}</strong>{label}</button>' for key, label, tone in DEMAND_FILTERS)

    proposal_cards = []
    for item in proposals:
        if not item.get("item"):
            proposal_cards.append(
                f'<article class="card"><div class="card-head"><span class="label">{_escape(item.get("floor", ""))}</span></div>'
                f'<p class="muted">{_escape(item.get("reason", "nenhum item desbloqueado"))}</p></article>')
            continue
        risk = str(item.get("risk", "baixo"))
        proposal_cards.append(
            f'<article class="card"><div class="card-head"><span class="label">{_escape(item.get("floor", ""))}</span>'
            f'{_badge("risco " + risk, "ok" if risk == "baixo" else "warn")}</div>'
            f'<div class="proposal-title"><span class="mono">{_escape(item["item"])}</span>'
            f'<h2>{_escape(_demand_title(str(item.get("title", ""))))}</h2></div>'
            f'<div class="chips sub">Depende de {_chips(list(item.get("deps", [])))}</div>'
            f'<p class="sub" style="margin:0">Motivo: {_escape(item.get("reason", ""))}</p>'
            f'<div class="actions"><button class="accept" data-item="{_escape(item["item"])}" '
            f'data-floor="{_escape(item.get("floor", ""))}" disabled>Aceitar</button>'
            '<span class="sub">envia ao orquestrador do andar</span></div></article>')
    ready = sum(1 for item in proposals if item.get("item"))
    proposal_html = (f'<div class="grid">{"".join(proposal_cards)}</div>' if proposal_cards else
                     '<div class="empty">Nenhuma proposta agora: todo andar tem trabalho ou não há item desbloqueado.</div>')

    if accounts:
        recommendation = next((row.get("recommendation", "") for row in accounts if row.get("watched")),
                              str(accounts[0].get("recommendation", "")).removeprefix("conta informativa; "))
        account_html = (f'<div class="banner"><strong>Recomendação</strong><span>{_escape(recommendation)}. '
                        'O Monitor só recomenda; a troca é sua.</span></div>'
                        f'<div class="grid">{"".join(_account_card(row, now) for row in accounts)}</div>')
    else:
        account_html = ('<div class="empty">Uso das contas indisponível: arquivo de status do Maestri não encontrado. '
                        'Os limites voltam no próximo ciclo em que a fonte responder.</div>')
    levels = [str(window.get("level")) for row in accounts for window in row.get("windows", [])]
    critical, warning = levels.count("CRITICO"), levels.count("AVISO")
    if critical:
        accounts_chip = _badge(f"{critical} crítica" if critical == 1 else f"{critical} críticas", "crit")
    elif warning:
        accounts_chip = _badge(f"{warning} em aviso", "warn")
    else:
        accounts_chip = _badge(str(len(accounts)) if accounts else "—", "neutral")

    local = now.astimezone()
    replacements = {
        "{{FLOOR_COUNT}}": str(len(floors)), "{{UPDATED_AT_ISO}}": _escape(now.isoformat()),
        "{{UPDATED_AT}}": _escape(local.strftime("%H:%M")), "{{SOURCE_CHIPS}}": source_html,
        "{{ALERT_STRIP}}": alert_html, "{{FLOOR_CARDS}}": floor_html,
        "{{TAB_FLOORS}}": _badge(str(len(floors)), "neutral"),
        "{{TAB_DEMANDS}}": _badge(str(len(rows)) if roadmap is not None else "—", "neutral"),
        "{{TAB_PROPOSALS}}": _badge(str(ready), "info" if ready else "neutral"), "{{TAB_ACCOUNTS}}": accounts_chip,
        "{{DEMAND_FILTERS}}": filter_html, "{{DEMAND_ROWS}}": demand_html,
        "{{PROPOSAL_CARDS}}": proposal_html, "{{ACCOUNT_CARDS}}": account_html,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def _paths(paths: dict[str, Path] | None) -> dict[str, Path | None]:
    home = Path.home()
    project_root = Path(__file__).resolve().parent
    runtime = project_root / "runtime"
    workspace_dir = home / ".maestri" / "workspaces"
    marker_dir = runtime / "marcadores"
    result: dict[str, Path | None] = {
        "repo": None, "project_root": project_root, "runtime": runtime,
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


def _source_ages(paths: dict[str, Path | None], now: datetime, unavailable: list[str]) -> list[tuple[str, str, bool]]:
    sources = []
    for label, key, prefix in (("workspace", "workspace", "workspace"), ("contas", "usage", "contas")):
        path = paths.get(key)
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if path else None
        except OSError:
            modified = None
        down = any(item.startswith(prefix) for item in unavailable)
        if modified is None or down:
            sources.append((label, "indisponível", False))
        else:
            minutes = max(0, int((now - modified).total_seconds() // 60))
            sources.append((label, f"{minutes} min", True))
    roadmap_down = "roadmap indisponível" in unavailable
    sources.append(("roadmap", "indisponível" if roadmap_down else "neste ciclo", not roadmap_down))
    return sources


def run_check(now: datetime | None = None, paths: dict[str, Path] | None = None,
              runner: Callable[..., Any] | None = None) -> MonitorResult:
    now = _utc(now)
    resolved = _paths(paths)
    runner = runner or subprocess.run
    runtime = resolved["runtime"]
    state_path = runtime / "state.json"
    panel_path = runtime / "painel.html"
    config = _read_json(resolved["config"]) or {}
    repo = resolved["repo"] or Path(config.get("repo_produto", ""))
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
        runner(["git", "-C", str(repo), "fetch", "origin", "dev"], cwd=str(repo), capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
    roadmap = None
    try:
        result = runner(["git", "-C", str(repo), "show", "origin/dev:docs/roadmap.md"], cwd=str(repo),
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if result.returncode == 0 and isinstance(result.stdout, str):
            roadmap = parse_roadmap(result.stdout)
        else:
            unavailable.append("roadmap indisponível")
    except (OSError, subprocess.SubprocessError):
        unavailable.append("roadmap indisponível")
    proposals = propose_items(roadmap or {}, config)
    _write_json(runtime / "propostas.json", {"generated_at": now.isoformat(), "proposals": proposals})
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
        rendered = _render(resolved["template"], now, _source_ages(resolved, now, unavailable), floor_views, roadmap,
                           proposals, account_rows, unavailable, floor_events + account_events)
    except (OSError, UnicodeError):
        rendered = _render(DEFAULT_TEMPLATE, now, _source_ages(resolved, now, unavailable), floor_views, roadmap,
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
