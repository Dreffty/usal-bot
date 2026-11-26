"""De qué va un correo sin abrirlo.

El texto de ejemplo es el formato exacto con el que Studium manda los avisos
de foro: navegación, enlace al hilo, título repetido, autor, fecha y rayas
antes de llegar a lo que de verdad dice.
"""

import unittest
from unittest import mock

from usalbot import resumen
from usalbot.models import Mail

AVISO_STUDIUM = (
    "PI-Teoría (2026-27) -> Foros -> Novedades y anuncios de los PROFESORES -> "
    "Disponible todo el material de la asignatura "
    "https://studium26.usal.es/mod/forum/discuss.php?d=1361#p1583 "
    "Disponible todo el material de la asignatura de MARÍA ANGÉLICA GONZÁLEZ "
    "ARRIETA - viernes, 11 de septiembre de 2026, 09:53 "
    "--------------------------------------------------------------------- "
    "Buenos días: Ya tenéis disponible todo el material de la asignatura. "
    "El miércoles comenzaremos con el tema 2, por lo que os recomiendo que no "
    "dejéis materia atrasada. La entrega de la práctica 1 es el 20 de marzo."
)


def correo(**kw):
    base = dict(id="x", subject="PI-Teoría (2026-27): Disponible todo el material "
                                "de la asignatura",
                sender="MARÍA ANGÉLICA GONZÁLEZ ARRIETA", sender_email="p@usal.es",
                snippet=AVISO_STUDIUM, ts=1)
    base.update(kw)
    return Mail(**base)


class TestLimpieza(unittest.TestCase):
    def setUp(self):
        self.limpio = resumen._quitar_cabeceras(AVISO_STUDIUM,
                                                correo().subject)

    def test_fuera_la_navegacion(self):
        self.assertNotIn("-> Foros ->", self.limpio)

    def test_fuera_el_enlace(self):
        self.assertNotIn("http", self.limpio)

    def test_fuera_la_fecha_de_envio(self):
        self.assertNotIn("11 de septiembre de 2026", self.limpio)
        self.assertNotIn("09:53", self.limpio)

    def test_fuera_el_saludo_y_las_rayas(self):
        self.assertFalse(self.limpio.lower().startswith("buenos días"))
        self.assertNotIn("-----", self.limpio)

    def test_no_se_come_el_cuerpo(self):
        """El título aparece también dentro de una frase del cuerpo; quitarlo
        de ahí dejaba 'Ya tenéis .' sin sentido."""
        self.assertIn("El miércoles comenzaremos con el tema 2", self.limpio)
        self.assertIn("20 de marzo", self.limpio)

    def test_texto_vacio(self):
        self.assertEqual(resumen._quitar_cabeceras("", "x"), "")
        self.assertEqual(resumen._quitar_cabeceras("   ", ""), "")

    def test_un_correo_normal_no_se_toca(self):
        normal = "Te confirmo la reunión del jueves a las 10 en el despacho 204."
        self.assertIn("despacho 204", resumen._quitar_cabeceras(normal, "Reunión"))


class TestExtracto(unittest.TestCase):
    def test_elige_lo_que_lleva_informacion(self):
        texto = resumen.extracto(correo())
        self.assertTrue(texto)
        self.assertNotIn("http", texto)
        self.assertNotIn("Foros", texto)

    def test_no_se_pasa_de_largo(self):
        self.assertLessEqual(len(resumen.extracto(correo())), resumen.MAXIMO + 1)

    def test_sin_cuerpo_no_inventa(self):
        self.assertEqual(resumen.extracto(correo(snippet="")), "")

    def test_descarta_el_pie_legal(self):
        pie = ("Este mensaje es confidencial. Antes de imprimir este correo "
               "piense en el medio ambiente.")
        self.assertEqual(resumen.extracto(correo(snippet=pie)), "")

    def test_correo_sin_palabras_clave_devuelve_algo(self):
        suelto = ("Os dejo por aquí unas reflexiones sobre la asignatura que "
                  "me parecieron interesantes tras la clase de ayer por la tarde.")
        self.assertTrue(resumen.extracto(correo(snippet=suelto)))


class TestModelo(unittest.IsolatedAsyncioTestCase):
    async def test_apagado_no_llama_a_nadie(self):
        with mock.patch.object(type(resumen.cfg), "llm_enabled", property(lambda s: False)):
            self.assertEqual(await resumen.con_modelo(correo()), "")

    async def test_si_falla_el_modelo_queda_el_extracto(self):
        with mock.patch("usalbot.resumen.con_modelo", mock.AsyncMock(return_value="")):
            texto = await resumen.resumir(correo())
        self.assertTrue(texto, "sin modelo tiene que quedar el resumen del texto")

    async def test_el_del_modelo_manda(self):
        with mock.patch("usalbot.resumen.con_modelo",
                        mock.AsyncMock(return_value="Piden entregar la práctica el día 20.")):
            self.assertEqual(await resumen.resumir(correo()),
                             "Piden entregar la práctica el día 20.")

    async def test_cuerpo_muy_corto_no_se_manda(self):
        self.assertEqual(await resumen.con_modelo(correo(snippet="ok")), "")


if __name__ == "__main__":
    unittest.main()
