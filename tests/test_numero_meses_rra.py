# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / "src"
        / "recupere_monday"
        / "extraction"
    ),
)

from numero_meses_rra import (  # noqa: E402
    MESES_BB_PATTERN,
    escolher_numero_meses_rra,
    extract_numero_meses_from_text,
    normalize_numero_meses_rra,
)


class TestNormalizeNumeroMesesRra(unittest.TestCase):
    def test_inteiro_limpo(self):
        self.assertEqual(normalize_numero_meses_rra("58"), "58")
        self.assertEqual(normalize_numero_meses_rra(58), "58")

    def test_ocr_bb_klug_virgula(self):
        self.assertEqual(normalize_numero_meses_rra("5,8"), "58")

    def test_ocr_ponto_ou_espaco(self):
        self.assertEqual(normalize_numero_meses_rra("5.8"), "58")
        self.assertEqual(normalize_numero_meses_rra("5 8"), "58")

    def test_vazio(self):
        self.assertIsNone(normalize_numero_meses_rra(None))
        self.assertIsNone(normalize_numero_meses_rra(""))


class TestExtracaoTextoBb(unittest.TestCase):
    def test_texto_bb_ocr_5_virgula_8(self):
        text = (
            "Valor do IR : R$ 60.873,83\n"
            "Numero de meses : 5,8\n"
            "Beneficiário : VANDERLEI KLUG\n"
        )
        self.assertEqual(
            extract_numero_meses_from_text(text, MESES_BB_PATTERN),
            "58",
        )

    def test_texto_bb_58_limpo(self):
        text = "Número de meses : 58\n"
        self.assertEqual(
            extract_numero_meses_from_text(text, MESES_BB_PATTERN),
            "58",
        )


class TestEscolherNumeroMesesRra(unittest.TestCase):
    def test_nao_troca_58_da_ia_por_5_truncado(self):
        self.assertEqual(
            escolher_numero_meses_rra(extraido_local="5", extraido_ia=58),
            "58",
        )

    def test_local_ocr_ja_reconstruido(self):
        self.assertEqual(
            escolher_numero_meses_rra(extraido_local="5,8", extraido_ia=58),
            "58",
        )

    def test_ia_truncada_mantem_local(self):
        self.assertEqual(
            escolher_numero_meses_rra(extraido_local="58", extraido_ia=5),
            "58",
        )

    def test_iguais(self):
        self.assertEqual(
            escolher_numero_meses_rra(extraido_local="12", extraido_ia=12),
            "12",
        )


if __name__ == "__main__":
    unittest.main()
