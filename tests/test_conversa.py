import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conversa import ConversationLog  # noqa: E402
from terminal_screen import detect_activity, extract_events, render_conversation  # noqa: E402


def resp(text):
    return {"k": "resp", "items": [{"t": "p", "n": "", "x": text}]}


def ordem(text):
    return {"k": "ordem", "x": text}


class EventsTests(unittest.TestCase):
    def test_ordens_e_respostas_na_ordem_da_tela_sem_a_caixa_de_entrada(self):
        sep = "─" * 30
        lines = ["● resposta antiga", "", "✻ Churned for 5s · done 1:00", "", "❯ teste", "", "  Ran 1 shell command", "",
                 "● Estou respondendo.", "  segue", "", sep, "❯ rascunho", sep]
        ev = extract_events(lines)
        self.assertEqual([(e["k"], e["lines"]) for e in ev], [
            ("resp", ["resposta antiga"]), ("ordem", ["teste"]), ("resp", ["Estou respondendo.", "segue"])])

    def test_ordem_de_varias_linhas(self):
        ev = extract_events(["❯ faca isto", "  e tambem aquilo", "", "● ok"])
        self.assertEqual(ev[0], {"k": "ordem", "lines": ["faca isto", "e tambem aquilo"]})

    def test_detecta_atividade_quando_trabalhando_e_nada_quando_pronto(self):
        self.assertEqual(detect_activity(["● x", "✻ Cooking… (12s · ↑ 1.2k tokens · esc to interrupt)"]),
                         "Cooking… (12s · ↑ 1.2k tokens · esc to interrupt)")
        self.assertEqual(detect_activity(["● x", "✻ Cooked for 25s · done 14:49", "❯ "]), "")

    def test_render_conversation_junta_ordem_resposta_e_atividade(self):
        raw = "\r\n".join(["❯ oi", "", "● ola", "  mundo", "", "✻ Cooking… (3s · esc to interrupt)"])
        events, activity = render_conversation(raw, 60, 20)
        self.assertEqual([e["k"] for e in events], ["ordem", "resp"])
        self.assertEqual(events[1]["items"], [{"t": "p", "n": "", "x": "ola mundo"}])
        self.assertTrue(activity.startswith("Cooking"))


class ConversationLogTests(unittest.TestCase):
    def test_guarda_as_respostas_que_saem_da_tela(self):
        log = ConversationLog()
        log.update("t", [resp("primeira")])
        out = log.update("t", [ordem("nova ordem"), resp("segunda")])  # a primeira saiu da tela
        self.assertEqual([e.get("x") or e["items"][0]["x"] for e in out], ["primeira", "nova ordem", "segunda"])

    def test_resposta_que_cresce_e_atualizada_no_lugar(self):
        log = ConversationLog()
        log.update("t", [resp("comeco")])
        grown = {"k": "resp", "items": [{"t": "p", "n": "", "x": "comeco"}, {"t": "p", "n": "", "x": "mais texto"}]}
        out = log.update("t", [grown])
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["items"]), 2)

    def test_nao_duplica_quando_a_tela_se_repete(self):
        log = ConversationLog()
        for _ in range(3):
            out = log.update("t", [ordem("a"), resp("b")])
        self.assertEqual(len(out), 2)

    def test_limite_e_separacao_por_terminal(self):
        log = ConversationLog(keep=3)
        for i in range(6):
            log.update("t1", [resp(f"r{i}")])
        log.update("t2", [resp("outro")])
        self.assertEqual([e["items"][0]["x"] for e in log.update("t1", [])], ["r3", "r4", "r5"])
        self.assertEqual(len(log.update("t2", [])), 1)

    def test_persiste_em_arquivo_e_recarrega(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "conversas.json"
            ConversationLog(path).update("t", [resp("guardada")])
            self.assertTrue(json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual(ConversationLog(path).update("t", [])[0]["items"][0]["x"], "guardada")


if __name__ == "__main__":
    unittest.main()


class FerramentasEParciaisTests(unittest.TestCase):
    def test_chamada_de_ferramenta_nao_e_resposta(self):
        lines = ["● Checar worktree do roteiro", "  ⎿  $ cd /c/x; git status", "     saida longa", "",
                 "● Running 1 shell command…", "", "● Resposta de verdade.", "  segue"]
        ev = extract_events(lines)
        self.assertEqual([e["lines"] for e in ev], [["Resposta de verdade.", "segue"]])

    def test_nota_do_sistema_na_ordem_e_ignorada(self):
        ev = extract_events(["❯ teste", "  ⎿  8 skills available", "", "● ok"])
        self.assertEqual(ev[0], {"k": "ordem", "lines": ["teste"]})

    def test_resposta_com_a_bolinha_fora_da_tela_vira_parcial_so_quando_permitido(self):
        lines = ["  meio de um paragrafo que continua", "  - item novo", "", "✻ Cooked for 3s"]
        self.assertEqual(extract_events(lines), [])
        ev = extract_events(lines, partial_top=True)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["parcial"])
        self.assertEqual(ev[0]["lines"], ["meio de um paragrafo que continua", "- item novo"])

    def test_log_completa_a_resposta_guardada_com_o_trecho_parcial(self):
        log = ConversationLog()
        inicio = {"k": "resp", "items": [{"t": "p", "n": "", "x": "Titulo da resposta"},
                                          {"t": "p", "n": "", "x": "Este paragrafo longo explica a situacao do repositorio"}]}
        log.update("t", [inicio])
        parcial = {"k": "resp", "parcial": True, "items": [
            {"t": "p", "n": "", "x": "Este paragrafo longo explica a situacao do repositorio e o que fazer depois"},
            {"t": "li", "n": "", "x": "item novo"}]}
        out = log.update("t", [parcial])
        self.assertEqual(len(out), 1)
        self.assertEqual([i["x"] for i in out[0]["items"]], [
            "Titulo da resposta", "Este paragrafo longo explica a situacao do repositorio e o que fazer depois", "item novo"])

    def test_parcial_sem_correspondencia_fica_como_entrada_propria(self):
        log = ConversationLog()
        out = log.update("t", [{"k": "resp", "parcial": True, "items": [{"t": "p", "n": "", "x": "trecho sem inicio conhecido"}]}])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["parcial"])


class RuidoDaInterfaceTests(unittest.TestCase):
    def test_pesquisa_de_satisfacao_nao_e_resposta(self):
        lines = ["● O texto e PAINEL 7421.", "", "✻ Cooked for 5s", "", "● How is Claude doing this session? (optional)",
                 "  1: Bad    2: Fine   3: Good   0: Dismiss"]
        self.assertEqual([e["lines"] for e in extract_events(lines)], [["O texto e PAINEL 7421."]])

    def test_historico_descarta_ruido_guardado_antes_do_filtro(self):
        log = ConversationLog()
        log.data["t"] = [{"k": "resp", "key": "r:How is", "items": [{"t": "p", "n": "", "x": "How is Claude doing this session? (optional)"}]},
                         {"k": "resp", "key": "r:ok", "items": [{"t": "p", "n": "", "x": "ok"}]}]
        out = log.update("t", [])
        self.assertEqual([e["items"][0]["x"] for e in out], ["ok"])
