"""Que el bot no te despierte por una newsletter, y si por un examen."""

import unittest

from usalbot.models import Mail
from usalbot.triage import fechas_mencionadas, puntuar


def correo(**kw) -> Mail:
    base = dict(id="x", subject="", sender="", sender_email="", snippet="", ts=0)
    base.update(kw)
    return Mail(**base)


class TestTriage(unittest.TestCase):
    def test_profesor_con_examen_es_clave(self):
        m = puntuar(correo(subject="Fecha del examen parcial",
                           sender_email="profesor@usal.es", unread=True))
        self.assertTrue(m.clave)
        self.assertIn("viene de una persona de la USAL", m.reasons)

    def test_newsletter_no_es_clave(self):
        m = puntuar(correo(subject="Newsletter semanal de ofertas",
                           sender_email="news@tienda.com",
                           snippet="Para darse de baja pulse aqui"))
        self.assertFalse(m.clave)

    def test_studium_puntua(self):
        m = puntuar(correo(subject="Nueva tarea disponible",
                           sender_email="noreply@studium.usal.es",
                           snippet="Se ha anadido una entrega con plazo"))
        self.assertTrue(m.clave)

    def test_fechas_detectadas(self):
        m = correo(subject="Entrega el 12/03", snippet="o como tarde el 5 de abril")
        fechas = fechas_mencionadas(m)
        self.assertIn("12/03", fechas)
        self.assertTrue(any("abril" in f for f in fechas))

    def test_sin_tildes_tambien_cuenta(self):
        a = puntuar(correo(subject="Calificación publicada", sender_email="p@usal.es"))
        b = puntuar(correo(subject="Calificacion publicada", sender_email="p@usal.es"))
        self.assertEqual(a.score, b.score)

    def test_score_nunca_negativo(self):
        m = puntuar(correo(subject="Promocion descuento webinar",
                           sender_email="spam@x.com", snippet="unsubscribe"))
        self.assertGreaterEqual(m.score, 0)


if __name__ == "__main__":
    unittest.main()


class TestUmbralUnico(unittest.TestCase):
    """El umbral estuvo escrito a mano en tres sitios y recalibrarlo no hacía
    nada. Esto comprueba que todos miran el mismo número."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from usalbot import db
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()

    def tearDown(self):
        from usalbot import db
        from usalbot.config import cfg
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    def test_el_modelo_y_la_base_coinciden(self):
        from usalbot import db
        from usalbot.models import UMBRAL_CLAVE

        justo_debajo = correo(id="bajo", subject="a", ts=100)
        justo_debajo.score = UMBRAL_CLAVE - 1
        justo_encima = correo(id="alto", subject="b", ts=100)
        justo_encima.score = UMBRAL_CLAVE
        db.guardar_correos([justo_debajo, justo_encima])

        self.assertFalse(justo_debajo.clave)
        self.assertTrue(justo_encima.clave)
        desde_la_base = db.correos(limite=10, solo_clave=True)
        self.assertEqual([m.id for m in desde_la_base], ["alto"])

    def test_los_pendientes_de_avisar_usan_el_mismo(self):
        from usalbot import db
        from usalbot.models import UMBRAL_CLAVE

        m = correo(id="x", subject="c", ts=1000)
        m.score = UMBRAL_CLAVE
        bajo = correo(id="y", subject="d", ts=1000)
        bajo.score = UMBRAL_CLAVE - 1
        db.guardar_correos([m, bajo])
        self.assertEqual([c.id for c in db.correos_clave_sin_notificar(0)], ["x"])


class TestFechasUtiles(unittest.TestCase):
    """La línea "menciona:" solo sirve si son fechas de verdad y están por
    venir. Salía "26-27" (un curso académico) y el propio día de envío."""

    def setUp(self):
        from datetime import date
        self.hoy = date(2026, 9, 11)

    def _vale(self, texto):
        from usalbot.triage import _es_fecha_util
        return _es_fecha_util(texto, self.hoy)

    def test_un_curso_academico_no_es_una_fecha(self):
        self.assertFalse(self._vale("26-27"))
        self.assertFalse(self._vale("2025-26"))

    def test_fechas_imposibles(self):
        self.assertFalse(self._vale("31/13"))
        self.assertFalse(self._vale("30 de febrero"))

    def test_lo_que_esta_por_venir(self):
        self.assertTrue(self._vale("14 de septiembre"))
        self.assertTrue(self._vale("5 de octubre"))

    def test_hoy_cuenta(self):
        self.assertTrue(self._vale("11 de septiembre"))

    def test_lo_de_anteayer_no(self):
        self.assertFalse(self._vale("9 de septiembre"))

    def test_sin_año_se_entiende_el_próximo(self):
        """En septiembre, "20 de marzo" es marzo del curso que entra."""
        self.assertTrue(self._vale("20 de marzo"))
        self.assertTrue(self._vale("12/03"))

    def test_en_un_correo_entero(self):
        # Una fecha siempre futura: las pasadas se descartan a propósito.
        from datetime import date, timedelta
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                 "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        d = date.today() + timedelta(days=10)
        futura = f"{d.day} de {meses[d.month - 1]}"
        m = correo(subject="Entrega ampliada", ts=1,
                   snippet=f"El curso 2026-27 empieza; entregad antes del {futura}")
        fechas = fechas_mencionadas(m)
        self.assertIn(futura, fechas)
        self.assertNotIn("26-27", fechas)
