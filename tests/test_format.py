"""El texto que se lee en el movil: que las fechas se cuenten como se hablan."""

import unittest
from datetime import datetime, timedelta

from usalbot.config import cfg
from usalbot.format import agenda_semana, cuanto_falta, lista_tareas, semaforo, trocear
from usalbot.models import Task


def tarea(horas: float, **kw) -> Task:
    venc = datetime.now(cfg.tz) + timedelta(hours=horas)
    base = dict(id="t", kind="assign", title="Práctica 1", course="Cálculo I",
                due=int(venc.timestamp()))
    base.update(kw)
    return Task(**base)


class TestFormato(unittest.TestCase):
    def test_semaforo_por_urgencia(self):
        self.assertEqual(semaforo(tarea(-5)), "⚠️")
        self.assertEqual(semaforo(tarea(5)), "🔴")
        self.assertEqual(semaforo(tarea(48)), "🟠")
        self.assertEqual(semaforo(tarea(100)), "🟡")
        self.assertEqual(semaforo(tarea(500)), "🟢")
        self.assertEqual(semaforo(Task(id="x", kind="assign", title="s", due=None)), "⚪")

    def test_cuanto_falta_en_castellano(self):
        self.assertIn("HOY", cuanto_falta(tarea(2)))
        self.assertIn("mañana", cuanto_falta(tarea(26)))
        self.assertIn("venció", cuanto_falta(tarea(-30)))
        self.assertEqual(cuanto_falta(Task(id="x", kind="assign", title="s", due=None)), "sin fecha")

    def test_lista_vacia_no_revienta(self):
        self.assertIn("✅", lista_tareas([], "Pendiente"))
        self.assertIn("✅", agenda_semana([]))

    def test_escapa_html(self):
        t = tarea(10, title="Tarea <script>alert(1)</script>")
        self.assertNotIn("<script>", lista_tareas([t], "x"))
        self.assertIn("&lt;script&gt;", lista_tareas([t], "x"))

    def test_trocear_respeta_el_limite(self):
        largo = "\n\n".join(["bloque de texto " * 20] * 40)
        trozos = trocear(largo)
        self.assertGreater(len(trozos), 1)
        self.assertTrue(all(len(t) <= 3900 for t in trozos))

    def test_trocear_no_pierde_bloques(self):
        largo = "\n\n".join(f"bloque {i} " + "x" * 300 for i in range(30))
        unido = " ".join(trocear(largo))
        for i in range(30):
            self.assertIn(f"bloque {i}", unido)


if __name__ == "__main__":
    unittest.main()


class TestNingunMensajeSePasaDelLimite(unittest.TestCase):
    """Telegram rechaza el mensaje ENTERO si pasa de 4096 caracteres o si el
    HTML está mal cerrado. Antes, un bloque largo se enviaba tal cual."""

    def test_bloque_mas_largo_que_el_tope(self):
        trozos = trocear("normal\n\n" + "X" * 5000)
        self.assertTrue(all(len(t) <= 3900 for t in trozos))

    def test_linea_gigante_no_deja_html_roto(self):
        trozos = trocear("a\n\n<b>" + "Y" * 5000 + "</b>")
        for t in trozos:
            self.assertEqual(t.count("<b>"), t.count("</b>"),
                             "una etiqueta a medias tumba el mensaje entero")

    def test_no_se_pierde_el_contenido_corto(self):
        self.assertEqual(trocear("hola"), ["hola"])

    def test_titulo_largo_de_tarea(self):
        from usalbot.models import Task
        t = Task(id="1", kind="assign", title="Entrega " + "muy larga " * 500,
                 course="X", due=2_000_000_000)
        for trozo in trocear(lista_tareas([t], "Pendiente")):
            self.assertLessEqual(len(trozo), 3900)


class TestAvisoDeFechaMovida(unittest.TestCase):
    def _tarea(self, horas):
        from usalbot.models import Task
        from datetime import datetime, timedelta
        venc = datetime.now(cfg.tz) + timedelta(hours=horas)
        return Task(id="1", kind="assign", title="Memoria", course="Redes",
                    due=int(venc.timestamp()))

    def test_adelanto_se_marca_distinto(self):
        from usalbot.format import fecha_movida
        import time
        ahora = int(time.time())
        self.assertIn("ADELANTADO", fecha_movida(self._tarea(10), ahora + 48 * 3600))
        self.assertNotIn("ADELANTADO", fecha_movida(self._tarea(48), ahora + 10 * 3600))

    def test_desde_sin_fecha(self):
        from usalbot.format import fecha_movida
        texto = fecha_movida(self._tarea(10), None)
        self.assertIn("sin fecha", texto)
