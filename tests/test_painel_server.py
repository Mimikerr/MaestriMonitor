import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / "painel_server.py"
SPEC = importlib.util.spec_from_file_location("painel_server", SCRIPT)
server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = server
SPEC.loader.exec_module(server)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class AcceptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.runtime = root / "runtime"
        self.config = root / "config.json"
        write_json(self.config, {"remetente": "Claude Code #4 | Monitor de Andares",
                                 "floors": [{"name": "Dev 3", "orquestrador": "Orquestrador 3"}]})
        write_json(self.runtime / "propostas.json", {"proposals": [
            {"floor": "Dev 3", "item": "R39", "title": "Tela X", "deps": ["R38"]}]})
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, returncode=0):
        def run(args, **kwargs):
            self.calls.append(args)
            return SimpleNamespace(returncode=returncode, stdout="", stderr="falha" if returncode else "")
        return run

    def accept(self, item="R39", floor="Dev 3", returncode=0):
        return server.accept(item, floor, self.runtime, self.config, self.runner(returncode), background=False)

    def status(self):
        return json.loads((self.runtime / "aceitas.json").read_text(encoding="utf-8"))

    def test_sends_prefixed_message_to_floor_orchestrator(self):
        self.accept()
        cli, verb, target, message = self.calls[0]
        self.assertEqual((verb, target), ("ask", "Orquestrador 3"))
        self.assertTrue(message.startswith("[Claude Code #4 | Monitor de Andares]"))
        self.assertIn("R39 Tela X", message)
        self.assertIn("R38", message)
        self.assertNotIn("\n", message)
        self.assertEqual(self.status()["R39"]["status"], "enviado")

    def test_failure_is_recorded_and_allows_retry(self):
        self.accept(returncode=1)
        self.assertEqual(self.status()["R39"]["status"], "falhou")
        self.accept()
        self.assertEqual(self.status()["R39"]["status"], "enviado")

    def test_duplicate_is_rejected(self):
        self.accept()
        with self.assertRaises(server.AcceptError) as error:
            self.accept()
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(len(self.calls), 1)

    def test_rejects_invalid_item_unknown_floor_and_stale_proposal(self):
        for item, floor, status in (("R39; rm", "Dev 3", 400), ("R39", "Dev 9", 400), ("R40", "Dev 3", 409),
                                    ("R39", "Ground", 400)):
            with self.assertRaises(server.AcceptError) as error:
                self.accept(item, floor)
            self.assertEqual(error.exception.status, status)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
