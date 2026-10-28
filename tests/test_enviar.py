import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import brainiac_source as src  # noqa: E402
import painel_brainiac as painel  # noqa: E402

CONFIG = {"projetos": [{"apelido": "p", "workspace": "Proj", "maestro": "Maestro"}]}


def runner_with(agent_status="running", sent=None):
    sent = sent if sent is not None else []

    def run(cmd, **_kw):
        method = cmd[cmd.index("call") + 1]
        params = json.loads(cmd[cmd.index("call") + 2])
        if method == "workspaces.list":
            out = [{"id": "w1", "name": "Proj"}]
        elif method == "agents.list":
            out = [{"id": "a1", "workspaceId": "w1", "name": "Maestro", "status": agent_status, "terminalId": "t1"},
                   {"id": "a2", "workspaceId": "w1", "name": "Outro", "status": "running", "terminalId": "t2"}]
        elif method == "terminals.input":
            sent.append(params)
            out = {"accepted": True}
        else:
            raise AssertionError(method)
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")
    return run, sent


class CleanMessageTests(unittest.TestCase):
    def test_vira_uma_linha_sem_controles(self):
        self.assertEqual(src.clean_message("  faca\nisso\x1b[31m agora\r\n "), "faca isso [31m agora")

    def test_vazio_e_longo_sao_recusados(self):
        for bad in ("", "  \n ", "x" * (src.MAX_MESSAGE + 1), None, 5):
            with self.assertRaises(src.SendError) as cm:
                src.clean_message(bad)
            self.assertEqual(cm.exception.status, 400)


class SendToMaestroTests(unittest.TestCase):
    def test_digita_no_terminal_do_maestro_e_confirma_com_enter(self):
        runner, sent = runner_with()
        out = src.send_to_maestro(CONFIG, "p", "rode os testes", runner=runner, pause=lambda _s: None)
        self.assertEqual(sent, [{"id": "t1", "data": "rode os testes"}, {"id": "t1", "data": "\r"}])
        self.assertEqual((out["ok"], out["maestro"], out["tamanho"]), (True, "Maestro", 14))

    def test_nunca_envia_a_outro_agente_do_projeto(self):
        runner, sent = runner_with()
        src.send_to_maestro(CONFIG, "p", "oi", runner=runner, pause=lambda _s: None)
        self.assertTrue(all(s["id"] == "t1" for s in sent))

    def test_projeto_desconhecido(self):
        runner, sent = runner_with()
        with self.assertRaises(src.SendError) as cm:
            src.send_to_maestro(CONFIG, "x", "oi", runner=runner)
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(sent, [])

    def test_maestro_parado_nao_envia(self):
        runner, sent = runner_with("stopped")
        with self.assertRaises(src.SendError) as cm:
            src.send_to_maestro(CONFIG, "p", "oi", runner=runner)
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(sent, [])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        cls._orig = painel.source.send_to_maestro
        painel.source.send_to_maestro = lambda cfg, alias, text, **_k: (
            cls.calls.append((alias, text)) or {"ok": True, "projeto": "Proj", "maestro": "Maestro", "tamanho": len(text)})
        painel.ROOT = Path(__file__).resolve().parent / "_rt"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), painel.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        painel.source.send_to_maestro = cls._orig
        rt = painel.ROOT
        if rt.exists():
            for f in rt.glob("*"):
                f.unlink()
            rt.rmdir()

    def post(self, headers):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/enviar", method="POST",
                                     data=json.dumps({"projeto": "p", "mensagem": "oi"}).encode(),
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_aceita_pedido_da_propria_pagina(self):
        self.calls.clear()
        self.assertEqual(self.post({"X-Painel": "1"}), 200)
        self.assertEqual(self.calls, [("p", "oi")])

    def test_recusa_sem_cabecalho_do_painel(self):
        self.calls.clear()
        self.assertEqual(self.post({}), 403)
        self.assertEqual(self.calls, [])

    def test_recusa_origem_de_outro_site(self):
        self.calls.clear()
        self.assertEqual(self.post({"X-Painel": "1", "Origin": "http://evil.example"}), 403)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
