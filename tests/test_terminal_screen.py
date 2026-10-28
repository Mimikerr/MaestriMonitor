import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from terminal_screen import Screen, render_terminal  # noqa: E402


class ScreenTests(unittest.TestCase):
    def test_posicionamento_absoluto_preserva_espacos(self):
        raw = "\x1b[2J\x1b[1;1HCodex\x1b[1;7Hquebra\x1b[2;3Hnao e"
        self.assertEqual(render_terminal(raw, 40, 5), ["Codex quebra", "  nao e"])

    def test_avanco_de_coluna_vira_espacos(self):
        self.assertEqual(render_terminal("a\x1b[3Cb", 20, 5), ["a   b"])

    def test_apagar_linha_e_reescrever(self):
        raw = "primeiro texto\r\x1b[2Ksegundo"
        self.assertEqual(render_terminal(raw, 40, 5), ["segundo"])

    def test_redesenho_por_cursor_acima(self):
        raw = "linha1\r\nlinha2\x1b[1A\r\x1b[2Kmudou\x1b[1B\r"
        self.assertEqual(render_terminal(raw, 40, 5), ["mudou", "linha2"])

    def test_rolagem_guarda_o_historico(self):
        raw = "\r\n".join(f"l{i}" for i in range(10))
        out = render_terminal(raw, 20, 5, keep=100)
        self.assertEqual(out[0], "l0")
        self.assertEqual(out[-1], "l9")

    def test_quebra_automatica_na_largura_do_terminal(self):
        self.assertEqual(render_terminal("a" * 25, 20, 5), ["a" * 20, "a" * 5])

    def test_ignora_cores_modos_e_titulo(self):
        raw = "\x1b[?25l\x1b]0;titulo\x07\x1b[38;2;1;2;3mok\x1b[0m\x1b[?25h"
        self.assertEqual(render_terminal(raw, 40, 5), ["ok"])

    def test_separador_longo_e_encurtado_e_vazio_nao_quebra(self):
        self.assertEqual(render_terminal("─" * 100, 120, 5), ["─" * 24])
        self.assertEqual(render_terminal("", 120, 5), [])
        self.assertEqual(Screen(5, 2).cols, 20)


if __name__ == "__main__":
    unittest.main()


class ResponsesTests(unittest.TestCase):
    def setUp(self):
        from terminal_screen import extract_responses
        self.extract = extract_responses

    def test_so_blocos_de_resposta_sem_ruido_da_interface(self):
        lines = [
            "● Primeira resposta.",
            "  continua aqui",
            "",
            "  - item da lista",
            "    com quebra",
            "",
            "  Listed 1 directory, ran 3 shell commands",
            "",
            "● Segunda resposta.",
            "",
            "✻ Churned for 51s · done 14:31",
            "                              bash denied by auto mode · /permissions",
            "────────",
            "❯",
        ]
        self.assertEqual(self.extract(lines), [
            "Primeira resposta.", "continua aqui", "", "- item da lista", "  com quebra", "", "Segunda resposta."])

    def test_chamada_de_ferramenta_com_bolinha_nao_e_resposta(self):
        self.assertEqual(self.extract(["● Bash(ls -la)", "  saida", "● Pronto."]), ["Pronto."])

    def test_aviso_alinhado_a_direita_nao_fecha_o_bloco(self):
        lines = ["● Resposta", "                                  Update available!", "  segue o texto"]
        self.assertEqual(self.extract(lines), ["Resposta", "segue o texto"])

    def test_sem_resposta_devolve_vazio(self):
        self.assertEqual(self.extract(["❯ ", "─────", "⏵⏵ auto mode on"]), [])
