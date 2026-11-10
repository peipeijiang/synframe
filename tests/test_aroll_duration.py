import unittest

from synframe.planning import PlanError, aroll_duration


class ArollDurationTest(unittest.TestCase):
    LINE = ("¿Buscas el detalle más divertido para Halloween? Te los pones, apagas la luz y proyectas figuras en la pared. "
            "Doce luces de colores en cada bolsa. ¡Pídelos ya en el carrito naranja!")

    def test_wave1_line_hugs_speech(self):
        # 32 words at 2.85 w/s is 11.2 s; wave1 asked for 15 s and got a garbled filler take.
        result = aroll_duration(self.LINE, "es-MX", 15)
        self.assertEqual(result["words"], 32)
        self.assertEqual(result["duration_sec"], 13)

    def test_short_line_respects_model_minimum(self):
        self.assertEqual(aroll_duration("¡Pídelos ya!", "es-MX", 15)["duration_sec"], 4)

    def test_too_long_line_must_be_split(self):
        with self.assertRaises(PlanError):
            aroll_duration(" ".join([self.LINE] * 2), "es-MX", 15)

    def test_unknown_locale_is_not_guessed(self):
        with self.assertRaises(PlanError):
            aroll_duration("hola", "pt-BR", 15)

    def test_punctuation_is_not_a_word(self):
        self.assertEqual(aroll_duration("hola , mundo !", "es-MX", 15)["words"], 2)


if __name__ == "__main__":
    unittest.main()
