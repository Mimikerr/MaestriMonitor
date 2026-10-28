import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import brainiac_source as src  # noqa: E402


def fake_runner(responses):
    def run(cmd, **_kw):
        method = cmd[cmd.index("call") + 1]
        value = responses[method]
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")
    return run


class CleanTerminalTests(unittest.TestCase):
    def test_remove_ansi_e_guarda_as_ultimas_linhas(self):
        raw = "\x1b[1;1H\x1b[38;2;1;2;3mprimeira\x1b[0m\r\n\x1b[2Ksegunda\r\n\r\nterceira"
        self.assertEqual(src.clean_terminal(raw, keep=2), ["segunda", "terceira"])

    def test_vazio(self):
        self.assertEqual(src.clean_terminal(""), [])


class ParsePlanTests(unittest.TestCase):
    def test_primeiro_pendente_vira_atual_quando_nao_ha_andamento(self):
        plan = src.parse_plan("- [x] a\n- [ ] b\n- [ ] c")
        self.assertEqual([s["state"] for s in plan["steps"]], ["done", "doing", "todo"])
        self.assertEqual((plan["done"], plan["total"]), (1, 3))

    def test_marcador_explicito_de_andamento_e_respeitado(self):
        plan = src.parse_plan("- [x] a\n- [ ] b\n- [~] c")
        self.assertEqual([s["state"] for s in plan["steps"]], ["done", "todo", "doing"])

    def test_sem_checklist(self):
        self.assertEqual(src.parse_plan("texto livre")["total"], 0)


class UsageTests(unittest.TestCase):
    def test_converte_fracao_em_percentual(self):
        state = {"accounts": [{"provider": "claude", "label": "Padrao", "phase": "ready", "snapshot": {
            "meters": [{"label": "Plano", "windows": [{"label": "Sessao", "usedFraction": 0.284, "resetsAt": "x"}]}]}},
            {"provider": "gemini", "snapshot": {"meters": []}}]}
        rows = src.usage_rows(state)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["windows"][0]["percent"], 28)


class SnapshotTests(unittest.TestCase):
    CONFIG = {"projetos": [{"apelido": "p", "workspace": "Proj", "maestro": "Maestro"},
                           {"apelido": "x", "workspace": "Inexistente", "maestro": "Z"}]}

    def responses(self):
        return {
            "workspaces.list": [{"id": "w1", "name": "Proj", "rootPath": "C:/nao/existe"}],
            "agents.list": [
                {"id": "a1", "workspaceId": "w1", "name": "Outro", "provider": "codex", "status": "stopped"},
                {"id": "a2", "workspaceId": "w1", "name": "Maestro", "provider": "claude", "status": "running",
                 "terminalId": "t1", "config": {"metadata": {"lastTurnEndedAt": "2026-10-28T12:00:00Z"}}},
            ],
            "terminals.scrollback": {"data": "\x1b[1mtrabalhando\x1b[0m"},
            "terminals.list": [{"id": "t1", "cols": 80, "rows": 24}],
            "usage.getState": {"accounts": []},
        }

    def test_monta_projetos_com_maestro_primeiro_e_terminal(self):
        now = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)
        snap = src.build_snapshot(self.CONFIG, runner=fake_runner(self.responses()), now=now)
        self.assertTrue(snap["ok"])
        proj = snap["projects"][0]
        self.assertEqual([a["nome"] for a in proj["agentes"]], ["Maestro", "Outro"])
        self.assertEqual(proj["agentes"][0]["terminal"], ["trabalhando"])
        self.assertEqual(proj["agentes"][0]["ultimo_turno"], "ha 2 h")
        self.assertFalse(snap["projects"][1]["encontrado"])

    def test_brainiac_fora_do_ar_marca_erro_sem_quebrar(self):
        resp = {"workspaces.list": OSError("sem app")}
        snap = src.build_snapshot(self.CONFIG, runner=fake_runner(resp))
        self.assertFalse(snap["ok"])
        self.assertIn("Brainiac indisponivel", snap["error"])

    def test_scrollback_so_e_lido_do_maestro_rodando(self):
        calls = []
        base = fake_runner(self.responses())

        def run(cmd, **kw):
            calls.append(cmd[cmd.index("call") + 1])
            return base(cmd, **kw)
        src.build_snapshot(self.CONFIG, runner=run)
        self.assertEqual(calls.count("terminals.scrollback"), 1)


if __name__ == "__main__":
    unittest.main()
