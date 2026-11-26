"""La pregunta abierta: claves, NLU, local y degradado sin red."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from usalbot import db, nlu
from usalbot import pregunta as qa
from usalbot.config import cfg
from usalbot.models import Mail, Task


async def _vivos_si(enlaces):
    """Los tests de `buscar_web` van sin red: todo se da por vivo."""
    return enlaces


class TestPregunta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()
        db.guardar_tareas([
            Task(id="cambio1", kind="assign",
                 title="Solicitud cambio de curso", course="Secretaría",
                 course_id=9, due=2_000_000_000),
        ])
        db.guardar_correos([
            Mail(id="m1", subject="Cambio de curso 2025-26: plazo abierto",
                 sender="Secretaría", sender_email="s@usal.es",
                 snippet="El plazo para pedir el cambio termina el 30 de septiembre",
                 ts=int(time.time()), score=8, reasons=["secretaria"],
                 unread=True, resumen=""),
        ])

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    def test_palabras_clave_quita_la_cortesia(self):
        claves = qa.palabras_clave(
            "¿Cómo puedo aplicar el cambio de curso del A al B?")
        self.assertIn("cambio", claves)
        for sobra in ("como", "puedo", "aplicar", "curso", "el"):
            self.assertNotIn(sobra, claves)

    def test_buscar_local_encuentra_tarea_y_correo(self):
        local = qa.buscar_local("cómo aplicar el cambio de curso")
        self.assertTrue(any("cambio" in t.title.lower() for t in local.tareas))
        self.assertTrue(any("cambio" in m.subject.lower() for m in local.correos))

    def test_responder_sin_red_tira_de_lo_local(self):
        async def web_caida(_pregunta):
            return [], [], True

        async def sin_modelo(*_a, **_k):
            return None

        with patch.object(qa, "buscar_web", side_effect=web_caida), \
             patch.object(qa, "_con_modelo", side_effect=sin_modelo):
            resp = asyncio.run(qa.responder_pregunta(
                "cómo aplicar el cambio de curso"))
        self.assertIn("Studium", resp)
        self.assertIn("cambio", resp.lower())
        self.assertIn("No he podido mirarlo ahora mismo", resp)

    def test_web_rota_no_tumba_la_respuesta(self):
        async def boom(*_a, **_k):
            raise ConnectionError("sin red")

        with patch.object(qa, "_usal", boom), \
             patch.object(qa, "_bing", boom), \
             patch.object(qa, "_ddg_json", boom), \
             patch.object(qa, "_vivos", _vivos_si):
            usal, general, caida = asyncio.run(qa.buscar_web("cambio de curso"))
        self.assertEqual((usal, general, caida), ([], [], True))

    def test_plural_encuentra_singular(self):
        ahora = int(time.time())
        db.guardar_tareas([Task(id="t9", kind="tutorial", title="Tutoría cálculo",
                                course="Cálculo", course_id=3,
                                due=ahora + 2 * 86400)])
        local = qa.buscar_local("¿Cuándo son las tutorías de cálculo?")
        self.assertTrue(any("Tutoría" in t.title for t in local.tareas))

    def test_correo_de_una_sola_palabra_floja_fuera(self):
        ahora = int(time.time())
        db.guardar_correos([
            Mail(id="solo-una", subject="Wifi del campus: corte el jueves",
                 sender="SI", sender_email="si@usal.es",
                 snippet="Corte del wifi el jueves", ts=ahora),
            Mail(id="las-dos", subject="Eduroam: configura el wifi en tu móvil",
                 sender="SI", sender_email="si@usal.es",
                 snippet="Guía para el wifi eduroam", ts=ahora),
        ])
        local = qa.buscar_local("¿Cómo me conecto al wifi eduroam?")
        ids = [m.id for m in local.correos]
        self.assertIn("las-dos", ids)
        self.assertNotIn("solo-una", ids)

    def test_palabras_web_sin_verbos_flojos(self):
        web = qa.palabras_web("¿Cómo veo mis notas del expediente en miUSAL?")
        self.assertNotIn("veo", web)
        self.assertIn("expediente", web)

    def test_palabras_web_sin_verbos_de_tramite(self):
        web = qa.palabras_web("¿Cómo solicito la beca general del Ministerio?")
        self.assertNotIn("solicito", web)
        self.assertEqual(web, ["beca", "general", "ministerio"])

    def test_foco_saca_lo_especifico(self):
        web = qa.palabras_web("¿Cómo me conecto al wifi eduroam en la USAL?")
        usal, general, caida = asyncio.run(qa.buscar_web(
            "¿Cómo me conecto al wifi eduroam en la USAL?"))
        self.assertFalse(caida)
        self.assertTrue(any("eduroam" in (e.titulo + e.url).lower()
                            for e in general),
                        f"sin eduroam en: {[e.titulo for e in general]}")
        urls = [e.url for e in general]
        self.assertEqual(len(urls), len(set(urls)))

    def test_pertinencia_frena_spam(self):
        spam = qa.Enlace(titulo="Heal The World - Michael Jackson (Letra)",
                         url="https://letras.example/x", resumen="letras")
        bueno = qa.Enlace(titulo="Beca general del Ministerio: plazos",
                          url="https://becas.example/x",
                          resumen="solicita tu beca general")
        claves = ["solicito", "beca", "general", "ministerio"]
        self.assertFalse(qa._pertinente(spam, claves))
        self.assertTrue(qa._pertinente(bueno, claves))

    def test_dedup_por_titulo(self):
        async def tres_iguales(_q):
            return [qa.Enlace(titulo=f"Cambio de curso {a}",
                              url=f"https://usal.es/x-{a}") for a in (2023, 2024, 2025)]

        async def vacio(_q):
            return []

        with patch.object(qa, "_usal", tres_iguales), \
             patch.object(qa, "_buscar", vacio), \
             patch.object(qa, "_vivos", _vivos_si):
            usal, _general, _caida = asyncio.run(
                qa.buscar_web("cambio curso"))
        self.assertEqual(len(usal), 1)

    def test_parseo_bing_y_usal_sin_red(self):
        bing = ('<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?x&amp;'
                'u=a1aHR0cHM6Ly9zdHVkaXVtLnVzYWwuZXMv">Studium USAL</a></h2>'
                '<div class="b_caption"><p>Campus virtual</p></div></li>')
        enlaces = qa._parsear_bing(bing)
        self.assertEqual(len(enlaces), 1)
        self.assertTrue(enlaces[0].url.startswith("https://studium"))
        self.assertIn("Studium", enlaces[0].titulo)
        usal = ('<h3 class="title"><a href="/tramites-administrativos-1">'
                'Trámites Administrativos</a></h3>')
        nodos = qa._parsear_usal(usal)
        self.assertEqual(len(nodos), 1)
        self.assertEqual(nodos[0].url,
                         "https://usal.es/tramites-administrativos-1")

    def test_palabras_web_conserva_curso(self):
        claves = qa.palabras_web(
            "¿Cómo puedo aplicar el cambio de curso del A al B?")
        self.assertIn("curso", claves)
        self.assertIn("cambio", claves)
        self.assertNotIn("puedo", claves)

    def test_nlu_entiende_la_duda_abierta(self):
        inten = asyncio.run(nlu.entender(
            "¿cómo puedo aplicar el cambio de curso del A al B?"))
        self.assertEqual(inten.nombre, "pregunta")

    def test_nlu_no_rompe_lo_concreto(self):
        # "Mis matrículas" sigue siendo asignaturas, no duda abierta.
        inten = asyncio.run(nlu.entender("¿cuáles son mis matrículas?"))
        self.assertEqual(inten.nombre, "asignaturas")

    def test_del_a_al_b_implica_grupo(self):
        web = qa.palabras_web(
            "¿Cómo puedo aplicar el cambio de curso del A al B?")
        self.assertIn("grupo", web)
        claves = qa.palabras_clave(
            "¿Cómo puedo aplicar el cambio de curso del A al B?")
        self.assertIn("grupo", claves)
        # Sin letras sueltas no se inventa nada.
        web2 = qa.palabras_web("¿Cuándo son las tutorías de cálculo?")
        self.assertNotIn("grupo", web2)

    def test_puerta_doble_para_spam_polisemico(self):
        divisa = qa.Enlace(titulo="Conversor de moneda | Cambio de Divisas",
                           url="https://divisas.example/c", resumen="convierte")

        async def una(_q):
            return [divisa]

        async def vacio(_q):
            return []

        with patch.object(qa, "_usal", vacio), \
             patch.object(qa, "_buscar", una), \
             patch.object(qa, "_vivos", _vivos_si):
            # Con 3 claves, casar solo "cambio" no basta.
            _u, general, _c = asyncio.run(qa.buscar_web(
                "¿Cómo puedo aplicar el cambio de curso del A al B?"))
            self.assertEqual(general, [])

    def test_ancla_distintiva_perdona_coincidencia_unica(self):
        biblio = qa.Enlace(titulo="Directorio de Bibliotecas de España",
                           url="https://www.ibibliotecas.com/",
                           resumen="Encuentra tu biblioteca")
        ruido = qa.Enlace(titulo="Stranger Things: la serie",
                          url="https://tv.example/x",
                          resumen="nuevos capítulos")

        async def una(_q):
            return [biblio, ruido]

        async def vacio(_q):
            return []

        with patch.object(qa, "_usal", vacio), \
             patch.object(qa, "_buscar", una), \
             patch.object(qa, "_vivos", _vivos_si):
            _u, general, _c = asyncio.run(qa.buscar_web(
                "dime bibliotecas cercanas a mi facultad"))
        urls = [e.url for e in general]
        self.assertIn("https://www.ibibliotecas.com/", urls)
        self.assertNotIn("https://tv.example/x", urls)

    def test_vivos_saca_muertos_y_deja_respuestas(self):
        # Servidor local: HEAD 200 determinista, sin depender de internet.
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            vivo = qa.Enlace(
                titulo="ok",
                url=f"http://127.0.0.1:{srv.server_port}/x")
            muerto = qa.Enlace(titulo="ko", url="http://nadie.invalid/y")
            res = asyncio.run(qa._vivos([vivo, muerto]))
        finally:
            srv.shutdown()
        self.assertEqual([e.titulo for e in res], ["ok"])

    def test_pegamento_fuera_de_web_y_local(self):
        web = qa.palabras_web(
            "¿Qué tengo que hacer para irme de Erasmus el curso que viene?")
        self.assertEqual(web, ["erasmus", "curso"])
        claves = qa.palabras_clave(
            "¿Qué tengo que hacer para irme de Erasmus el curso que viene?")
        self.assertEqual(claves, ["erasmus"])

    def test_matricula_sin_plazo_entra_igual(self):
        ahora = int(time.time())
        db.guardar_tareas([Task(id="mat1", kind="assign",
                                title="Matrícula 2025-26: cita de automatrícula",
                                course="Secretaría", course_id=9,
                                due=ahora + 30 * 86400)])
        db.guardar_correos([
            Mail(id="mat2", subject="Matrícula 2025-26: tu cita es el 15 de julio",
                 sender="Secretaría", sender_email="s@usal.es",
                 snippet="Cita de automatrícula el 15 de julio", ts=ahora),
        ])
        local = qa.buscar_local("¿Cuál es el plazo de matrícula de este curso?")
        self.assertTrue(any("automatrícula" in t.title for t in local.tareas))
        self.assertTrue(any("cita" in m.subject for m in local.correos))

    def test_usal_con_filtro_frena_basura(self):
        async def basura(_q):
            return [qa.Enlace(titulo="Información sobre el nuevo coronavirus",
                              url="https://usal.es/coronavirus"),
                    qa.Enlace(titulo="Grado en Trabajo Social",
                              url="https://usal.es/grado-trabajo-social")]

        async def vacio(_q):
            return []

        with patch.object(qa, "_usal", basura), \
             patch.object(qa, "_buscar", vacio), \
             patch.object(qa, "_vivos", _vivos_si):
            usal, _general, caida = asyncio.run(qa.buscar_web(
                "¿Qué tengo que hacer para irme de Erasmus el curso que viene?"))
        self.assertFalse(caida)
        self.assertEqual(usal, [])

    def test_general_ordena_por_relevancia(self):
        flojo = qa.Enlace(titulo="Estado de tu expediente de extranjería",
                          url="https://extranjeria.example/a", resumen="")
        bueno = qa.Enlace(titulo="Notas del expediente académico en miUSAL",
                          url="https://miusal.example/b", resumen="consulta tus notas")
        claves = ["notas", "expediente", "miusal"]
        self.assertGreater(qa._puntos(bueno, claves), qa._puntos(flojo, claves))

        async def dos(_q):
            return [flojo, bueno]

        async def vacio(_q):
            return []

        with patch.object(qa, "_usal", vacio), \
             patch.object(qa, "_buscar", dos), \
             patch.object(qa, "_vivos", _vivos_si):
            _usal, general, _caida = asyncio.run(qa.buscar_web(
                "¿Cómo veo mis notas del expediente en miUSAL?"))
        self.assertEqual(general[0].url, "https://miusal.example/b")

    def test_fuentes_citan_con_enlace_directo(self):
        local = qa.Local(
            tareas=[Task(id="t", kind="assign", title="Solicitud cambio",
                         course="Secretaría",
                         url="https://studium.usal.es/mod/assign/view.php?id=1")],
            correos=[Mail(id="m", subject="Cambio de curso: plazo abierto",
                          sender="Secretaría", snippet="...")])
        bloque = qa._bloque_fuentes(
            local, [qa.Enlace(titulo="Trámites", url="https://usal.es/tramites")],
            [])
        self.assertIn("🔗", bloque)
        self.assertIn("https://studium.usal.es/mod/assign/view.php?id=1", bloque)
        self.assertIn("https://usal.es/tramites", bloque)
        self.assertIn("Cambio de curso: plazo abierto", bloque)

    def test_pie_solo_cuando_hay_datos_propios(self):
        vacio = qa.Local(claves=["mundial", "futbol"])
        sin_pie = qa._plantilla("¿Quién ganó el mundial?", vacio, [], [], False)
        self.assertNotIn("plazos", sin_pie)
        con_correo = qa.Local(
            correos=[Mail(id="m", subject="Beca", sender="Becas", snippet="...")],
            claves=["beca"])
        con_pie = qa._plantilla("¿Cómo pido la beca?", con_correo, [], [], False)
        self.assertIn("plazos", con_pie)

    def test_respuesta_redactada_cita_fuentes(self):
        async def web(_p):
            return ([qa.Enlace(titulo="Becas USAL",
                               url="https://usal.es/becas")], [], False)

        async def modelo(_p, _c):
            return "Pide la beca en la sede del Ministerio."

        with patch.object(qa, "buscar_web", side_effect=web), \
             patch.object(qa, "_con_modelo", side_effect=modelo), \
             patch.object(qa, "leer_pagina", return_value=""):
            resp = asyncio.run(qa.responder_pregunta(
                "¿Cómo solicito la beca general del Ministerio?"))
        self.assertIn("Pide la beca", resp)
        self.assertIn("https://usal.es/becas", resp)


if __name__ == "__main__":
    unittest.main()
