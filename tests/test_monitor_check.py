import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "monitor_check.py"
SPEC = importlib.util.spec_from_file_location("monitor_check", SCRIPT)
monitor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = monitor
SPEC.loader.exec_module(monitor)


NOW = datetime(2026, 10, 23, 17, 0, tzinfo=timezone.utc)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class MonitorCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.usage = self.root / "usage.json"
        self.workspace = self.root / "workspace.json"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.floor2 = self.root / "floor2"
        self.floor3 = self.root / "floor3"
        self.floor2.mkdir()
        self.floor3.mkdir()
        self.config = self.root / "monitor-config.json"
        write_json(self.config, {
            "workspace_name": "Smart-balança",
            "floors": [
                {"name": "Ground", "branch": "dev", "worktree": str(self.repo), "track": ["R23", "R24"]},
                {"name": "Dev 2", "branch": "dev-home", "worktree": str(self.floor2), "track": ["R29", "R30"]},
                {"name": "Dev 3", "branch": "dev-3", "worktree": str(self.floor3), "track": ["R38", "R39"]},
            ],
            "items": {"R31": {"risks": ["schema"]}, "R39": {"risks": ["container"]}},
            "contas_vigiadas": ["Claude Code", "Codex", "Squad IA"],
            "thresholds": {"warning": 75, "critical": 90, "stalled_minutes": 30, "reminder_minutes": 120},
        })
        self.workspace_data = {
            "payload": {
                "name": "Smart-balança",
                "floors": [
                    {"name": "Dev 2", "branchName": "dev-home", "status": {"state": "working", "text": "R29 working"}, "nodes": []},
                    {"name": "Dev 3", "branchName": "dev-3", "status": {"state": "working", "text": "R38 working"}, "nodes": []},
                ],
                "nodes": [],
            }
        }
        write_json(self.workspace, self.workspace_data)
        self.usage.write_text(json.dumps({"providers": []}), encoding="utf-8")
        self.runner_calls = []
        self.head = "abc123"
        self.status_hash = "## dev"

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, args, **kwargs):
        self.runner_calls.append(args)
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""
        if args == ["git", "worktree", "list", "--porcelain"]:
            Result.stdout = (
                f"worktree {self.repo}\nbranch refs/heads/dev\n\n"
                f"worktree {self.floor2}\nbranch refs/heads/dev-home\n\n"
                f"worktree {self.floor3}\nbranch refs/heads/dev-3\n"
            )
        elif args[-2:] == ["rev-parse", "HEAD"]:
            Result.stdout = f"{self.head}\n" if self.head else ""
            Result.returncode = 0 if self.head else 1
        elif args[:2] == ["git", "-C"] and args[-3:] == ["status", "--short", "--branch"]:
            Result.stdout = self.status_hash or ""
            Result.returncode = 0 if self.status_hash is not None else 1
        if args[-1] == "origin/dev:docs/roadmap.md":
            Result.stdout = self.roadmap
        return Result()

    def run_check(self):
        return monitor.run_check(
            now=NOW,
            paths={"usage": self.usage, "workspace": self.workspace, "runtime": self.runtime,
                   "repo": self.repo, "config": self.config},
            runner=self.runner,
        )

    def set_roadmap(self, text):
        self.roadmap = text

    def test_no_anomaly_returns_one_and_emits_no_events(self):
        self.set_roadmap("- [x] [a1] R23 pronto\n- [ ] [b2] R24 pendente · dep R23")
        self.head = None
        self.status_hash = None
        result = self.run_check()
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.events, [])
        panel = (self.runtime / "painel.html").read_text(encoding="utf-8")
        self.assertIn("ocioso", panel)

    def test_missing_status_floor_with_signal_change_is_working(self):
        self.set_roadmap("")
        self.head = "new-head"
        self.status_hash = "## dev\n M file.kt"
        result = self.run_check()
        self.assertFalse(any(event.kind == "parado" for event in result.events))
        panel = (self.runtime / "painel.html").read_text(encoding="utf-8")
        self.assertIn("Ground</td><td>working", panel)

    def test_missing_status_floor_unchanged_for_30_minutes_is_stalled(self):
        self.set_roadmap("")
        state_path = self.runtime / "state.json"
        signals = {"status": None, "head": "abc123",
                   "git_status": hashlib.sha256(b"## dev").hexdigest(), "lastActiveAt": None}
        write_json(state_path, {"floors": {"Ground": {
            "signals": signals, "last_change": (NOW - timedelta(minutes=31)).isoformat()
        }}, "events": {}})
        result = self.run_check()
        self.assertTrue(any(event.kind == "parado" and event.target == "Ground" for event in result.events))

    def test_missing_status_and_no_signals_or_reservation_is_idle(self):
        self.set_roadmap("")
        self.head = None
        self.status_hash = None
        result = self.run_check()
        self.assertFalse(any(event.target == "Ground" for event in result.events))
        panel = (self.runtime / "painel.html").read_text(encoding="utf-8")
        self.assertIn("Ground</td><td>ocioso", panel)

    def test_missing_status_floor_with_roadmap_reservation_is_working(self):
        self.set_roadmap("- [~] **R24 — Room v6 (Ground).** Em andamento (térreo).")
        self.head = None
        self.status_hash = None
        result = self.run_check()
        self.assertFalse(any(event.kind == "parado" for event in result.events))
        panel = (self.runtime / "painel.html").read_text(encoding="utf-8")
        self.assertIn("Ground</td><td>working", panel)

    def test_working_floor_only_stalls_at_30_minutes(self):
        self.set_roadmap("")
        state_path = self.runtime / "state.json"
        floor_statuses = {"Ground": None, **{
            row["name"]: row["status"] for row in self.workspace_data["payload"]["floors"]
        }}
        seeded = {name: {"signals": {"status": monitor._status_signature(status),
                                     "head": "abc123", "lastActiveAt": None},
                         "last_change": (NOW - timedelta(minutes=29)).isoformat()}
                  for name, status in floor_statuses.items()}
        write_json(state_path, {"floors": seeded, "events": {}})
        self.run_check()
        self.assertFalse(any(e.kind == "parado" for e in self.run_check().events))
        data = json.loads(state_path.read_text(encoding="utf-8"))
        for item in data["floors"].values():
            item["last_change"] = (NOW - timedelta(minutes=31)).isoformat()
        write_json(state_path, data)
        result = self.run_check()
        self.assertTrue(any(e.kind == "parado" for e in result.events))

    def test_blocked_floor_at_31_minutes_is_travado(self):
        self.workspace_data["payload"]["floors"][0]["status"]["state"] = "blocked"
        write_json(self.workspace, self.workspace_data)
        status = self.workspace_data["payload"]["floors"][0]["status"]
        write_json(self.runtime / "state.json", {"floors": {"Dev 2": {
                    "signals": {"status": monitor._status_signature(status), "head": "abc123",
                                "git_status": None, "lastActiveAt": None},
                    "last_change": (NOW - timedelta(minutes=31)).isoformat()}}, "events": {}})
        self.set_roadmap("")
        result = self.run_check()
        self.assertTrue(any(e.kind == "travado" and e.target == "Dev 2" for e in result.events))

    def test_review_is_suppressed_by_marker(self):
        self.workspace_data["payload"]["floors"][0]["status"]["state"] = "review"
        write_json(self.workspace, self.workspace_data)
        self.set_roadmap("")
        self.assertTrue(any(e.kind == "review" for e in self.run_check().events))
        marker_dir = self.root / "marcadores"
        marker_dir.mkdir()
        (marker_dir / "Dev2.avisado").write_text("seen", encoding="utf-8")
        self.config_data = json.loads(self.config.read_text(encoding="utf-8"))
        self.config_data["markers_dir"] = str(marker_dir)
        write_json(self.config, self.config_data)
        write_json(self.runtime / "state.json", {"floors": {}, "events": {}})
        self.assertFalse(any(e.kind == "review" for e in self.run_check().events))

    def test_repeated_nonworking_error_status_for_30_minutes_is_travado(self):
        self.workspace_data["payload"]["floors"][0]["status"] = {
            "state": "error", "text": "socket timeout", "progress": 0,
        }
        write_json(self.workspace, self.workspace_data)
        status = self.workspace_data["payload"]["floors"][0]["status"]
        write_json(self.runtime / "state.json", {"floors": {"Dev 2": {
            "signals": {"status": monitor._status_signature(status), "head": "abc123",
                        "git_status": None, "lastActiveAt": None},
            "last_change": (NOW - timedelta(minutes=31)).isoformat(),
            "status_last_change": (NOW - timedelta(minutes=31)).isoformat(),
        }}, "events": {}})
        self.set_roadmap("")
        self.head = "new-head"
        result = self.run_check()
        self.assertTrue(any(event.kind == "travado" and event.target == "Dev 2" for event in result.events))

    def test_account_thresholds_and_rhythm(self):
        status = {"providers": [{"name": "Claude Code", "plan": "Pro", "state": "active", "meters": [
            {"windows": [{"id": "session", "ring": "session", "usedPercent": 74, "resetsAt": "2026-10-23T18:00:00Z", "durationSeconds": 18000},
                         {"id": "weekly", "ring": "weekly", "usedPercent": 75, "resetsAt": "2026-10-25T00:00:00Z", "durationSeconds": 604800}]}
        ]}, {"name": "Squad IA", "plan": "Pro", "state": "active", "meters": [
            {"windows": [{"id": "weekly", "ring": "weekly", "usedPercent": 90, "resetsAt": "2026-10-25T00:00:00Z", "durationSeconds": 604800}]}
        ]}]}
        write_json(self.usage, status)
        self.set_roadmap("")
        result = self.run_check()
        self.assertFalse(any(e.target.startswith("Claude Code (Pro) session") for e in result.events))
        self.assertTrue(any(e.level == "AVISO" for e in result.events))
        self.assertTrue(any(e.level == "CRITICO" for e in result.events))
        self.assertTrue(any(e.kind == "ritmo" for e in result.events))

    def test_reminder_after_two_hours_and_worsening_resends(self):
        self.set_roadmap("")
        status = {"providers": [{"name": "Claude Code", "plan": "Pro", "state": "active", "meters": [
            {"windows": [{"id": "weekly", "ring": "weekly", "usedPercent": 80, "resetsAt": "2026-10-24T17:00:00Z", "durationSeconds": 604800}]}
        ]}]}
        write_json(self.usage, status)
        first = self.run_check()
        self.assertEqual(len(first.events), 1)
        data = json.loads((self.runtime / "state.json").read_text(encoding="utf-8"))
        event = next(iter(data["events"].values()))
        event["last_sent"] = (NOW - timedelta(minutes=119)).isoformat()
        write_json(self.runtime / "state.json", data)
        self.assertEqual(self.run_check().events, [])
        data = json.loads((self.runtime / "state.json").read_text(encoding="utf-8"))
        next(iter(data["events"].values()))["last_sent"] = (NOW - timedelta(minutes=120)).isoformat()
        write_json(self.runtime / "state.json", data)
        self.assertEqual(len(self.run_check().events), 1)
        status["providers"][0]["meters"][0]["windows"][0]["usedPercent"] = 90
        write_json(self.usage, status)
        self.assertTrue(any(e.level == "CRITICO" for e in self.run_check().events))

    def test_roadmap_dependencies_and_reservations_are_not_proposed(self):
        text = """- [ ] [abcd] R23 pending\n- [x] [abcd] R24 ready\n- [ ] [abcd] R29 blocked · dep R23\n- [~] [abcd] R30 reserved (Dev 2) · dep R24\n- [ ] [abcd] R38 ready"""
        parsed = monitor.parse_roadmap(text)
        self.assertEqual(parsed["R29"]["state"], "todo")
        self.assertTrue(parsed["R30"]["reserved"])
        proposals = monitor.propose_items(parsed, json.loads(self.config.read_text(encoding="utf-8")))
        proposed = {row["floor"]: row["item"] for row in proposals}
        self.assertNotEqual(proposed.get("Dev 2"), "R29")
        self.assertNotEqual(proposed.get("Dev 2"), "R30")

    def test_schema_proposals_are_serialized_and_reserved_schema_blocks_all(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["floors"][0]["track"] = ["R20"]
        config["floors"][1]["track"] = ["R24"]
        config["items"].update({"R20": {"risks": ["schema"]}, "R24": {"risks": ["schema"]}})
        roadmap = monitor.parse_roadmap("- [ ] **R20 — Histórico Room.**\n- [ ] **R24 — Impressora Room.**")
        proposals = monitor.propose_items(roadmap, config)
        self.assertEqual(sum(item["risk"] == "schema" for item in proposals), 1)
        roadmap = monitor.parse_roadmap("- [ ] **R20 — Histórico Room.**\n- [~] **R24 — Impressora Room (Ground).**")
        proposals = monitor.propose_items(roadmap, config)
        self.assertFalse(any(item["item"] == "R20" for item in proposals))

    def test_unwatched_high_usage_account_is_visible_without_events(self):
        self.set_roadmap("")
        status = {"providers": [{"name": "Antigravity", "id": "antigravity", "plan": "Pro", "meters": [
            {"windows": [{"id": "weekly", "ring": "weekly", "usedPercent": 94,
                          "resetsAt": "2026-10-25T00:00:00Z", "durationSeconds": 604800}]}
        ]}]}
        write_json(self.usage, status)
        result = self.run_check()
        self.assertFalse(any(event.target.startswith("Antigravity") for event in result.events))
        panel = (self.runtime / "painel.html").read_text(encoding="utf-8")
        self.assertIn("Antigravity (Pro)", panel)
        self.assertIn("94%", panel)

    def test_unreadable_roadmap_keeps_panel_and_marks_source_unavailable(self):
        self.runner = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable"))
        result = self.run_check()
        self.assertEqual(result.exit_code, 1)
        panel = (self.runtime / "painel.html").read_text(encoding="utf-8")
        for tab in ("Andares", "Demandas", "Propostos", "Limites das contas"):
            self.assertIn(tab, panel)
        self.assertIn("indisponível", panel.lower())
        self.assertNotIn("client_secret", panel.lower())
        self.assertNotIn("access_token", panel.lower())


if __name__ == "__main__":
    unittest.main()
