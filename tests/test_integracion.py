"""Recorrido completo sin tocar la red.

Se le dan a la capa de Studium exactamente las respuestas que devuelve Moodle
(las claves y los tipos son los de core_calendar_get_action_events_by_timesort)
y se comprueba que al otro lado sale un mensaje de Telegram correcto.
"""

import asyncio
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from usalbot import db, format as fmt, sync
from usalbot.config import cfg
from usalbot.models import Mail

AHORA = int(time.time())


def evento(id_, nombre, curso_id, curso, dentro_de_horas, modulo="assign",
           actionable=True, overdue=False, eventtype="due"):
    return {
        "id": id_, "name": f"{nombre} vence", "eventtype": eventtype,
        "timestart": AHORA + int(dentro_de_horas * 3600),
        "timesort": AHORA + int(dentro_de_horas * 3600),
        "modulename": modulo, "instance": id_ * 10, "activityname": nombre,
        "overdue": overdue,
        "course": {"id": curso_id, "fullname": curso, "shortname": curso[:4]},
        "action": {"name": "Añadir entrega", "itemcount": 1, "actionable": actionable,
                   "url": f"https://studium.usal.es/mod/{modulo}/view.php?id={id_}"},
        "url": f"https://studium.usal.es/mod/{modulo}/view.php?id={id_}",
    }


RESPUESTAS = {
    "core_webservice_get_site_info": {"userid": 7, "fullname": "Alumno Ejemplo",
                                      "username": "aejemplo"},
    "core_enrol_get_users_courses": [
        {"id": 12, "fullname": "Cálculo I", "shortname": "CAL1", "hidden": 0},
        {"id": 13, "fullname": "Programación II", "shortname": "PRG2", "hidden": 0},
        {"id": 99, "fullname": "Curso viejo", "shortname": "OLD", "hidden": 1},
    ],
    # El inventario de la asignatura: trae TODO, con fecha y sin ella.
    "mod_assign_get_assignments": {"courses": [
        {"id": 12, "fullname": "Cálculo I", "assignments": [
            {"id": 500, "cmid": 5000, "name": "Trabajo voluntario", "duedate": 0},
            {"id": 501, "cmid": 5001, "name": "Práctica 2", "duedate": AHORA + 7200},
            {"id": 502, "cmid": 5002, "name": "Entrega ya hecha",
             "duedate": AHORA + 99999},
        ]},
        {"id": 13, "fullname": "Programación II", "assignments": [
            {"id": 510, "cmid": 5010, "name": "Lecturas recomendadas", "duedate": 0},
        ]},
    ]},
    "mod_quiz_get_quizzes_by_courses": {"quizzes": [
        {"id": 600, "coursemodule": 6000, "course": 13,
         "name": "Autoevaluación libre", "timeclose": 0},
    ]},
    "core_calendar_get_action_events_by_timesort": {"events": [
        evento(1, "Práctica 2", 12, "Cálculo I", 2),
        evento(2, "Cuestionario tema 3", 13, "Programación II", 30, modulo="quiz"),
        evento(3, "Entrega final", 13, "Programación II", 200),
        evento(4, "Ya entregada", 12, "Cálculo I", 50, actionable=False),
        evento(5, "Corregir entregas", 12, "Cálculo I", 5, eventtype="gradingdue"),
        evento(6, "Se me pasó", 12, "Cálculo I", -20, overdue=True),
    ]},
}


SIN_MODELO = mock.patch("usalbot.resumen.con_modelo", mock.AsyncMock(return_value=""))


async def falso_llamar(self, funcion, **params):
    if funcion not in RESPUESTAS:
        raise AssertionError(f"llamada inesperada: {funcion}")
    return RESPUESTAS[funcion]


CORREOS = [
    Mail(id="gmail:a", subject="Cambio de aula del examen del jueves",
         sender="Profesora García", sender_email="profe.ejemplo@usal.es",
         snippet="El examen del 12/03 se traslada al aula 204", ts=AHORA - 3600, unread=True),
    Mail(id="gmail:b", subject="Newsletter de descuentos",
         sender="Tienda", sender_email="news@tienda.com",
         snippet="darse de baja aqui", ts=AHORA - 7200),
]


class TestRecorridoCompleto(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    async def _sincronizar(self):
        with mock.patch("usalbot.sources.studium.Studium.llamar", falso_llamar), \
             SIN_MODELO, \
             mock.patch("usalbot.sources.mail.recientes",
                        mock.AsyncMock(return_value=[Mail(**vars(m)) for m in CORREOS])):
            return await sync.sincronizar()

    async def test_anade_las_tareas_sin_fecha(self):
        """Lo que el profesor cuelga sin fecha no sale en la línea de tiempo."""
        await self._sincronizar()
        titulos = {t.title for t in db.tareas_pendientes(limite=50)}
        self.assertIn("Trabajo voluntario", titulos)
        self.assertIn("Lecturas recomendadas", titulos)
        self.assertIn("Autoevaluación libre", titulos)

    async def test_no_inventa_pendientes_ya_entregados(self):
        """Una entrega con fecha futura que no está en la línea de tiempo es
        que ya la hiciste: Moodle la quita al entregar. Meterla sería mentir."""
        await self._sincronizar()
        titulos = {t.title for t in db.tareas_pendientes(limite=50)}
        self.assertNotIn("Entrega ya hecha", titulos)

    async def test_no_duplica_lo_que_ya_esta(self):
        """'Práctica 2' sale en las dos fuentes y debe aparecer una sola vez."""
        await self._sincronizar()
        practicas = [t for t in db.tareas_pendientes(limite=50)
                     if t.title == "Práctica 2"]
        self.assertEqual(len(practicas), 1)

    async def test_las_sin_fecha_no_disparan_avisos(self):
        from usalbot import alerts
        await self._sincronizar()
        sin_fecha = [t for t in db.tareas_pendientes(limite=50) if t.due is None]
        self.assertTrue(sin_fecha)
        for t in sin_fecha:
            self.assertIsNone(alerts.tramo_de(t))

    async def test_sincroniza_y_filtra(self):
        res = await self._sincronizar()
        self.assertEqual(res.errores, [])

        titulos = {t.title for t in db.tareas_pendientes(limite=50)}
        self.assertIn("Práctica 2", titulos)
        self.assertIn("Cuestionario tema 3", titulos)
        self.assertIn("Se me pasó", titulos)          # vencida, sigue contando
        self.assertNotIn("Ya entregada", titulos)      # actionable=False
        self.assertNotIn("Corregir entregas", titulos) # es cosa del profesor

    async def test_cursos_ocultos_fuera(self):
        await self._sincronizar()
        nombres = {c.name for c in db.cursos()}
        self.assertEqual(nombres, {"Cálculo I", "Programación II"})

    async def test_orden_por_fecha(self):
        await self._sincronizar()
        pendientes = db.tareas_pendientes(limite=50)
        con_fecha = [t.due for t in pendientes if t.due is not None]
        self.assertEqual(con_fecha, sorted(con_fecha))
        self.assertEqual(pendientes[0].title, "Se me pasó")  # lo vencido, primero

    async def test_las_sin_fecha_van_al_final(self):
        """Sin plazo no hay urgencia: estorban arriba de la lista."""
        await self._sincronizar()
        pendientes = db.tareas_pendientes(limite=50)
        sin_fecha = [i for i, t in enumerate(pendientes) if t.due is None]
        con_fecha = [i for i, t in enumerate(pendientes) if t.due is not None]
        self.assertTrue(min(sin_fecha) > max(con_fecha))

    async def test_entregar_algo_lo_saca_de_pendientes(self):
        await self._sincronizar()
        original = RESPUESTAS["core_calendar_get_action_events_by_timesort"]
        RESPUESTAS["core_calendar_get_action_events_by_timesort"] = {
            "events": [e for e in original["events"] if e["activityname"] != "Práctica 2"]
        }
        try:
            res = await self._sincronizar()
            self.assertGreaterEqual(res.entregadas, 1)
            self.assertNotIn("Práctica 2", {t.title for t in db.tareas_pendientes(limite=50)})
        finally:
            RESPUESTAS["core_calendar_get_action_events_by_timesort"] = original

    async def test_solo_avisa_una_vez_por_tramo(self):
        from usalbot import alerts
        await self._sincronizar()
        pendientes = db.tareas_pendientes(limite=50)
        primera_ronda = alerts.avisos_a_enviar(pendientes)
        self.assertTrue(primera_ronda)
        for t, tramo in primera_ronda:
            alerts.confirmar(t, tramo)
        self.assertEqual(alerts.avisos_a_enviar(pendientes), [])

    async def test_correo_clave_se_separa_del_ruido(self):
        await self._sincronizar()
        clave = db.correos(limite=10, solo_clave=True)
        self.assertEqual([m.subject for m in clave],
                         ["Cambio de aula del examen del jueves"])

    async def test_el_mensaje_final_se_lee(self):
        await self._sincronizar()
        texto = fmt.lista_tareas(db.tareas_pendientes(limite=10), "Lo que tienes pendiente")
        self.assertIn("Práctica 2", texto)
        self.assertIn("Cálculo I", texto)
        self.assertIn("HOY", texto)              # vence en 2 horas
        self.assertIn("studium.usal.es", texto)  # con enlace directo
        self.assertLess(len(texto), 4096)

    async def test_resumen_diario_completo(self):
        await self._sincronizar()
        texto = fmt.resumen_diario(db.tareas_pendientes(limite=40), db.correos(limite=10))
        self.assertIn("Se te ha pasado", texto)
        self.assertIn("Hoy", texto)
        self.assertIn("Correos que conviene mirar", texto)

    async def test_studium_caido_no_tumba_el_bot(self):
        async def explota(self, funcion, **params):
            raise ConnectionError("sin red")
        with mock.patch("usalbot.sources.studium.Studium.llamar", explota), \
             mock.patch("usalbot.sources.mail.recientes", mock.AsyncMock(return_value=[])):
            res = await sync.sincronizar()
        self.assertTrue(res.hubo_error)
        self.assertIn("Studium", res.errores[0])

    async def test_token_caducado_se_explica(self):
        from usalbot.sources.studium import StudiumError

        async def caducado(self, funcion, **params):
            raise StudiumError("ficha no valida", "invalidtoken")
        with mock.patch("usalbot.sources.studium.Studium.llamar", caducado), \
             mock.patch("usalbot.sources.mail.recientes", mock.AsyncMock(return_value=[])):
            res = await sync.sincronizar()
        self.assertIn("setup_wizard.py studium", res.errores[0])


if __name__ == "__main__":
    unittest.main()


class TestMensajesDeError(unittest.IsolatedAsyncioTestCase):
    """Un error de permisos no es una clave caducada: decirlo mal manda al
    usuario a rehacer la clave para nada."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    async def _error(self, codigo, mensaje="algo"):
        from usalbot.sources.studium import StudiumError

        async def falla(self, funcion, **params):
            raise StudiumError(mensaje, codigo)

        with mock.patch("usalbot.sources.studium.Studium.llamar", falla), \
             mock.patch("usalbot.sources.mail.recientes", mock.AsyncMock(return_value=[])):
            return await sync.sincronizar()

    async def test_clave_caducada(self):
        res = await self._error("invalidtoken")
        self.assertIn("setup_wizard.py studium", res.errores[0])

    async def test_funcion_no_expuesta_no_culpa_a_la_clave(self):
        res = await self._error("accessexception")
        self.assertNotIn("setup_wizard", res.errores[0])
        self.assertIn("No es culpa de tu clave", res.errores[0])


class TestLimiteDeMoodle(unittest.IsolatedAsyncioTestCase):
    """Moodle rechaza la consulta si se le piden más de 50 eventos:
    'Limit must be between 1 and 50 (inclusive)'."""

    async def test_nunca_pide_mas_de_50(self):
        from usalbot.sources.studium import Studium

        visto = {}

        async def espia(self, funcion, **params):
            visto.update(params)
            return {"events": []}

        with mock.patch("usalbot.sources.studium.Studium.llamar", espia):
            await Studium("https://x", "t").pendientes(limite=999)
        self.assertLessEqual(visto["limitnum"], 50)
        self.assertGreaterEqual(visto["limitnum"], 1)

    async def test_el_valor_por_defecto_tambien_cabe(self):
        from usalbot.sources.studium import Studium

        visto = {}

        async def espia(self, funcion, **params):
            visto.update(params)
            return {"events": []}

        with mock.patch("usalbot.sources.studium.Studium.llamar", espia):
            await Studium("https://x", "t").pendientes()
        self.assertLessEqual(visto["limitnum"], 50)


class TestManejadorDeErrores(unittest.IsolatedAsyncioTestCase):
    """Dos copias del bot a la vez es el fallo más fácil de provocar y el que
    peor se explicaba: una traza de veinte líneas y ninguna pista."""

    async def _mensaje_de(self, error):
        from telegram.ext import ContextTypes
        from usalbot.bot import al_fallar

        contexto = mock.Mock(spec=ContextTypes.DEFAULT_TYPE)
        contexto.error = error
        with mock.patch("usalbot.bot.log") as registro:
            await al_fallar(None, contexto)
        llamadas = (registro.error.call_args_list + registro.warning.call_args_list
                    + registro.exception.call_args_list)
        self.assertTrue(llamadas, "tenía que registrar algo")
        return " ".join(str(c) for c in llamadas)

    async def test_dos_copias_a_la_vez(self):
        from telegram.error import Conflict
        texto = await self._mensaje_de(Conflict("terminated by other getUpdates"))
        self.assertIn("OTRA copia", texto)
        self.assertIn("launchctl", texto)

    async def test_red_caida_es_solo_un_aviso(self):
        from telegram.error import NetworkError
        texto = await self._mensaje_de(NetworkError("sin red"))
        self.assertIn("Se reintenta solo", texto)

    async def test_token_malo_dice_donde_arreglarlo(self):
        from telegram.error import InvalidToken
        texto = await self._mensaje_de(InvalidToken("malo"))
        self.assertIn("BotFather", texto)

    async def test_error_desconocido_no_se_traga(self):
        texto = await self._mensaje_de(ValueError("algo raro"))
        self.assertIn("no previsto", texto)


class TestHechoYFechasMovidas(unittest.IsolatedAsyncioTestCase):
    """Dos fallos que solo aparecen al pasar una segunda sincronización."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()
        self.ahora = int(time.time())

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    def _tarea(self, horas, id_="ev:1", titulo="Práctica 4"):
        from usalbot.models import Task
        return Task(id=id_, kind="assign", title=titulo, course="Física",
                    due=self.ahora + int(horas * 3600))

    async def test_hecho_sobrevive_al_sync(self):
        """/hecho ponía done=1 y el UPSERT siguiente lo pisaba con el 0 que
        venía de Studium: la tarea volvía a la lista en 20 minutos."""
        db.guardar_tareas([self._tarea(40)])
        db.marcar_hecha("ev:1")
        self.assertEqual(db.tareas_pendientes(), [])
        db.guardar_tareas([self._tarea(40)])
        self.assertEqual(db.tareas_pendientes(), [], "no debe resucitar")

    async def test_hecho_no_avisa_mas(self):
        from usalbot import alerts
        db.guardar_tareas([self._tarea(10)])
        db.marcar_hecha("ev:1")
        db.guardar_tareas([self._tarea(10)])
        self.assertEqual(alerts.avisos_a_enviar(db.tareas_pendientes()), [])

    async def test_mover_la_fecha_rearma_los_avisos(self):
        """Plazo ampliado y vuelto a acercar: tenía que volver a avisar."""
        from usalbot import alerts
        db.guardar_tareas([self._tarea(20)])
        primeros = alerts.avisos_a_enviar(db.tareas_pendientes())
        self.assertTrue(primeros)
        for t, tramo in primeros:
            alerts.confirmar(t, tramo)

        db.guardar_tareas([self._tarea(14 * 24)])   # amplían dos semanas
        db.guardar_tareas([self._tarea(20)])        # y vuelve a acercarse
        self.assertTrue(alerts.avisos_a_enviar(db.tareas_pendientes()),
                        "con fecha nueva hay que volver a avisar")

    async def test_la_misma_fecha_no_rearma_nada(self):
        from usalbot import alerts
        db.guardar_tareas([self._tarea(20)])
        for t, tramo in alerts.avisos_a_enviar(db.tareas_pendientes()):
            alerts.confirmar(t, tramo)
        db.guardar_tareas([self._tarea(20)])
        self.assertEqual(alerts.avisos_a_enviar(db.tareas_pendientes()), [],
                         "sin cambio de fecha no se repite el aviso")

    async def test_detecta_y_cuenta_las_movidas(self):
        db.guardar_tareas([self._tarea(48)])
        nuevas, movidas = db.guardar_tareas([self._tarea(10)])
        self.assertEqual(nuevas, [])
        self.assertEqual(len(movidas), 1)
        tarea, antes = movidas[0]
        self.assertEqual(tarea.id, "ev:1")
        self.assertEqual(antes, self.ahora + 48 * 3600)

    async def test_el_aviso_distingue_adelanto_de_prorroga(self):
        adelantada = fmt.fecha_movida(self._tarea(10), self.ahora + 48 * 3600)
        ampliada = fmt.fecha_movida(self._tarea(48), self.ahora + 10 * 3600)
        self.assertIn("ADELANTADO", adelantada)
        self.assertNotIn("ADELANTADO", ampliada)

    async def test_una_tarea_nueva_no_cuenta_como_movida(self):
        nuevas, movidas = db.guardar_tareas([self._tarea(10)])
        self.assertEqual(len(nuevas), 1)
        self.assertEqual(movidas, [])


class TestConteoDeEntregadas(unittest.IsolatedAsyncioTestCase):
    """Las tareas sin fecha no salen en la línea de tiempo, así que contarlas
    antes de completar la lista las daba por entregadas en CADA pasada."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    async def test_las_sin_fecha_no_cuentan_como_entregadas(self):
        res = await self._sincronizar()
        self.assertEqual(res.entregadas, 0,
                         "nadie ha entregado nada en la primera pasada")
        res2 = await self._sincronizar()
        self.assertEqual(res2.entregadas, 0,
                         "ni en la segunda: la lista no ha cambiado")

    async def _sincronizar(self):
        with mock.patch("usalbot.sources.studium.Studium.llamar", falso_llamar), \
             SIN_MODELO, \
             mock.patch("usalbot.sources.mail.recientes", mock.AsyncMock(return_value=[])):
            return await sync.sincronizar()


class TestHechoAmbiguo(unittest.IsolatedAsyncioTestCase):
    """Con las tareas reales de un curso, "/hecho practica" empata en tres.
    Elegir una a ciegas deja sin avisos algo que no se ha entregado."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()
        from usalbot.models import Task
        ahora = int(time.time())
        db.guardar_tareas([
            Task(id="a", kind="assign", title="Práctica 4: Video",
                 course="Física", due=ahora + 40 * 3600),
            Task(id="b", kind="assign", title="Solicitud de cambio de subgrupo prácticas",
                 course="Coordinación", due=ahora + 60 * 3600),
            Task(id="c", kind="quiz", title="Test Autoevaluación Tema 5",
                 course="Computadores", due=ahora + 80 * 3600),
        ])

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    async def _hecho(self, texto):
        from telegram.ext import ContextTypes
        from usalbot.bot import cmd_hecho

        enviados = []
        update = mock.MagicMock()
        update.effective_message.reply_text = mock.AsyncMock(
            side_effect=lambda t, **k: enviados.append(t))
        contexto = mock.Mock(spec=ContextTypes.DEFAULT_TYPE)
        contexto.args = texto.split()
        await cmd_hecho(update, contexto)
        return " ".join(enviados)

    async def test_ambiguo_pregunta_en_vez_de_adivinar(self):
        respuesta = await self._hecho("practica")
        self.assertIn("¿Cuál?", respuesta)
        self.assertEqual(len(db.tareas_pendientes()), 3, "no debe marcar nada")

    async def test_sin_ambiguedad_marca(self):
        respuesta = await self._hecho("practica 4 video")
        self.assertIn("Marcada como hecha", respuesta)
        self.assertEqual(len(db.tareas_pendientes()), 2)

    async def test_deshacer_devuelve_la_tarea(self):
        from telegram.ext import ContextTypes
        from usalbot.bot import cmd_deshacer

        await self._hecho("practica 4 video")
        self.assertEqual(len(db.tareas_pendientes()), 2)

        enviados = []
        update = mock.MagicMock()
        update.effective_message.reply_text = mock.AsyncMock(
            side_effect=lambda t, **k: enviados.append(t))
        contexto = mock.Mock(spec=ContextTypes.DEFAULT_TYPE)
        await cmd_deshacer(update, contexto)
        self.assertIn("vuelve a estar pendiente", " ".join(enviados))
        self.assertEqual(len(db.tareas_pendientes()), 3)

    async def test_deshacer_sin_nada_que_deshacer(self):
        from telegram.ext import ContextTypes
        from usalbot.bot import cmd_deshacer

        enviados = []
        update = mock.MagicMock()
        update.effective_message.reply_text = mock.AsyncMock(
            side_effect=lambda t, **k: enviados.append(t))
        contexto = mock.Mock(spec=ContextTypes.DEFAULT_TYPE)
        await cmd_deshacer(update, contexto)
        self.assertIn("nada reciente", " ".join(enviados))

    async def test_lo_que_no_existe(self):
        self.assertIn("No sé cuál es", await self._hecho("cristalografía"))


class TestBibliotecasNoSonTareas(unittest.IsolatedAsyncioTestCase):
    """"dime bibliotecas cercanas a mi facultad" devolvía la lista de
    pendientes: sin regla que la reconozca caía en "proximas" por defecto."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()
        from usalbot.models import Task
        ahora = int(time.time())
        db.guardar_tareas([
            Task(id="t", kind="assign", title="Entrega práctica de redes",
                 course="Redes", due=ahora + 5 * 86400),
        ])

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    async def _texto(self, texto, intencion):
        from usalbot.bot import texto_libre

        enviados = []
        update = mock.MagicMock()
        update.effective_message.text = texto
        update.effective_message.reply_text = mock.AsyncMock(
            side_effect=lambda t, **k: enviados.append(t))
        update.effective_chat.send_action = mock.AsyncMock()
        contexto = mock.Mock()
        contexto.args = []
        with mock.patch("usalbot.bot.nlu.entender",
                        mock.AsyncMock(return_value=intencion)), \
             mock.patch("usalbot.bot._asegurar_datos",
                        mock.AsyncMock()), \
             mock.patch("usalbot.bot.qa.responder_pregunta",
                        mock.AsyncMock(return_value="RESPUESTA ABIERTA")) as abierta:
            await texto_libre(update, contexto)
        return " ".join(enviados), abierta

    async def test_buscar_va_directo_a_pregunta_abierta(self):
        from usalbot.nlu import Intencion
        # Todo texto libre es búsqueda abierta (Studium + correo + web):
        # ya no hay "no hay nada" local antes de la web.
        respuesta, abierta = await self._texto(
            "dime bibliotecas cercanas a mi facultad",
            Intencion(nombre="buscar",
                      filtro="bibliotecas cercanas facultad"))
        abierta.assert_awaited_once_with(
            "dime bibliotecas cercanas a mi facultad")
        self.assertIn("RESPUESTA ABIERTA", respuesta)
        self.assertNotIn("redes", respuesta)

    async def test_cuando_tambien_es_pregunta_abierta(self):
        from usalbot.nlu import Intencion
        # "cuándo se entrega X" también va a la abierta: esta ya incluye
        # lo local (tareas), así que no se pierde nada.
        respuesta, abierta = await self._texto(
            "cuándo se entrega la práctica de redes",
            Intencion(nombre="cuando", filtro="redes"))
        abierta.assert_awaited_once_with(
            "cuándo se entrega la práctica de redes")
        self.assertIn("RESPUESTA ABIERTA", respuesta)

    async def test_tarea_explicita_sigue_yendo_a_pendientes(self):
        from usalbot.nlu import Intencion
        # "búscame la tarea con la fecha más próxima" sí es local.
        respuesta, abierta = await self._texto(
            "búscame la tarea con la fecha más próxima",
            Intencion(nombre="proximas"))
        abierta.assert_not_awaited()
        self.assertIn("redes", respuesta)


class TestUnaSincronizacionALaVez(unittest.IsolatedAsyncioTestCase):
    """Tres mensajes seguidos al arrancar lanzaban tres sincronizaciones
    enteras en paralelo: nueve llamadas a Studium para traer lo mismo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()
        sync._en_curso = None

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()
        sync._en_curso = None

    async def test_las_simultaneas_se_enganchan_a_la_primera(self):
        import asyncio

        llamadas = []

        async def lento(self, funcion, **params):
            llamadas.append(funcion)
            await asyncio.sleep(0.2)
            if funcion == "core_webservice_get_site_info":
                return {"userid": 7, "fullname": "X"}
            return [] if funcion == "core_enrol_get_users_courses" else {"events": []}

        with mock.patch("usalbot.sources.studium.Studium.llamar", lento), \
             mock.patch("usalbot.sources.mail.recientes", mock.AsyncMock(return_value=[])):
            resultados = await asyncio.gather(*[sync.sincronizar() for _ in range(3)])

        self.assertLessEqual(len(llamadas), 4, "no debe multiplicar las llamadas")
        self.assertEqual(len(resultados), 3, "las tres esperas reciben respuesta")
        self.assertTrue(all(r is not None for r in resultados))

    async def test_despues_se_puede_volver_a_sincronizar(self):
        """Que no se quede bloqueado para siempre tras la primera."""
        with mock.patch("usalbot.sources.studium.Studium.llamar", falso_llamar), \
             SIN_MODELO, \
             mock.patch("usalbot.sources.mail.recientes", mock.AsyncMock(return_value=[])):
            await sync.sincronizar()
            segunda = await sync.sincronizar()
        self.assertIsNotNone(segunda)


class TestEventosRotos(unittest.IsolatedAsyncioTestCase):
    """Un evento con un campo raro tumbaba la lista entera: la sincronización
    fallaba con KeyError y no se guardaba ninguna tarea."""

    ROTOS = {"events": [
        {"id": 1, "activityname": "Buena", "timesort": 2_000_000_000,
         "course": {"id": 1, "fullname": "Física"}, "modulename": "assign"},
        {"id": 2, "timesort": 2_000_000_000},                     # sin nombre
        {"activityname": "Sin id", "timesort": 2_000_000_000},    # sin id
        {"id": 4, "activityname": "Curso nulo", "course": None},
        {"id": 5, "activityname": "Fecha rara", "timesort": "no es un número"},
        {"id": 6, "activityname": "Otra buena", "timesort": 2_100_000_000,
         "course": {"id": 2, "fullname": "Redes"}, "modulename": "quiz"},
    ]}

    async def _pendientes(self):
        from usalbot.sources.studium import Studium

        async def falso(self, funcion, **params):
            return TestEventosRotos.ROTOS if "calendar" in funcion else {"userid": 1}

        with mock.patch("usalbot.sources.studium.Studium.llamar", falso):
            return await Studium("https://x", "t").pendientes()

    async def test_no_revienta(self):
        import logging
        logging.disable(logging.WARNING)
        try:
            tareas = await self._pendientes()
        finally:
            logging.disable(logging.NOTSET)
        self.assertTrue(tareas, "las buenas tienen que llegar igual")

    async def test_las_buenas_sobreviven(self):
        import logging
        logging.disable(logging.WARNING)
        try:
            titulos = {t.title for t in await self._pendientes()}
        finally:
            logging.disable(logging.NOTSET)
        self.assertIn("Buena", titulos)
        self.assertIn("Otra buena", titulos)

    async def test_se_descarta_lo_que_no_se_puede_identificar(self):
        import logging
        logging.disable(logging.WARNING)
        try:
            titulos = {t.title for t in await self._pendientes()}
        finally:
            logging.disable(logging.NOTSET)
        self.assertNotIn("Sin id", titulos, "sin id no hay forma de seguirla")
        self.assertNotIn("", titulos, "sin nombre no sirve de nada")

    async def test_una_fecha_ilegible_no_inventa_plazo(self):
        import logging
        logging.disable(logging.WARNING)
        try:
            tareas = {t.title: t for t in await self._pendientes()}
        finally:
            logging.disable(logging.NOTSET)
        self.assertIsNone(tareas["Fecha rara"].due)
