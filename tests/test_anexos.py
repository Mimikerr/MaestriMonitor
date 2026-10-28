import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anexos  # noqa: E402
import painel_brainiac as painel  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
GIF = b"GIF89a" + b"\x00" * 20
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 20


class DetectTests(unittest.TestCase):
    def test_reconhece_pelo_conteudo(self):
        self.assertEqual([anexos.detect_image(x) for x in (PNG, JPG, GIF, WEBP)], ["png", "jpg", "gif", "webp"])

    def test_recusa_o_que_nao_e_imagem_mesmo_com_nome_de_imagem(self):
        for bad in (b"MZ\x90\x00 executavel", b"<svg onload=alert(1)>", b"<html>", b""):
            self.assertIsNone(anexos.detect_image(bad))


class SaveTests(unittest.TestCase):
    def test_salva_com_extensao_detectada_e_nome_unico(self):
        with tempfile.TemporaryDirectory() as d:
            a = anexos.save_image(PNG, Path(d) / "sub")
            b = anexos.save_image(PNG, Path(d) / "sub")
            self.assertTrue(a.name.endswith(".png") and a.read_bytes() == PNG)
            self.assertNotEqual(a, b)

    def test_erros(self):
        with tempfile.TemporaryDirectory() as d:
            for data, status in ((b"", 400), (b"texto", 415), (PNG + b"\x00" * anexos.MAX_BYTES, 413)):
                with self.assertRaises(anexos.AnexoError) as cm:
                    anexos.save_image(data, Path(d))
                self.assertEqual(cm.exception.status, status)

    def test_limpeza_remove_so_os_antigos(self):
        with tempfile.TemporaryDirectory() as d:
            old = anexos.save_image(PNG, Path(d))
            new = anexos.save_image(JPG, Path(d))
            week = time.time() - 9 * 86400
            os.utime(old, (week, week))
            self.assertEqual(anexos.cleanup(Path(d)), 1)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        painel.ROOT = Path(cls.tmp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), painel.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def post(self, data, headers):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/anexo", method="POST", data=data, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_salva_imagem_da_propria_pagina_e_devolve_o_caminho(self):
        status, body = self.post(PNG, {"X-Painel": "1", "Content-Type": "image/png"})
        self.assertEqual(status, 200)
        self.assertTrue(Path(body["caminho"]).is_file())
        self.assertIn("anexos", body["caminho"])

    def test_recusa_sem_cabecalho_do_painel_ou_de_outro_site(self):
        self.assertEqual(self.post(PNG, {})[0], 403)
        self.assertEqual(self.post(PNG, {"X-Painel": "1", "Origin": "http://evil.example"})[0], 403)

    def test_recusa_arquivo_que_nao_e_imagem(self):
        status, body = self.post(b"MZ conteudo", {"X-Painel": "1"})
        self.assertEqual(status, 415)
        self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main()
