"""El calendario exportado de Moodle, con sus rarezas de formato."""

import unittest

from usalbot.sources.studium_ical import (_desescapar, _eventos, _fecha,
                                          _partir_asunto, convertir, cursos_de)

# Tal y como lo escribe Moodle: lineas largas partidas, tildes y escapes.
CALENDARIO = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//Moodle Pty Ltd//NONSGML Moodle Version 2025100700//EN\r\n"
    "METHOD:PUBLISH\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:1234567@studium26.usal.es\r\n"
    "SUMMARY:Práctica 2: sockets (Redes de Computadores) se debe entregar\r\n"
    "DESCRIPTION:Entregad el fichero comprimido\\, sin ejecutables\; y con\r\n"
    "  vuestro nombre dentro.\r\n"
    "DTSTART:20260315T235900Z\r\n"
    "DTEND:20260315T235900Z\r\n"
    "CATEGORIES:Redes de Computadores\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:7654321@studium26.usal.es\r\n"
    "SUMMARY:Test tema 6 (Cálculo I) se cierra\r\n"
    "DTSTART:20260320T180000Z\r\n"
    "CATEGORIES:Cálculo I\r\n"
    "URL:https://studium26.usal.es/mod/quiz/view.php?id=99\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:1111@studium26.usal.es\r\n"
    "SUMMARY:Reunión de delegados\r\n"
    "DTSTART:20260322\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


class TestParseo(unittest.TestCase):
    def test_encuentra_los_eventos(self):
        self.assertEqual(len(_eventos(CALENDARIO)), 3)

    def test_junta_las_lineas_partidas(self):
        ev = _eventos(CALENDARIO)[0]
        self.assertIn("vuestro nombre dentro", ev["DESCRIPTION"])

    def test_deshace_los_escapes(self):
        ev = _eventos(CALENDARIO)[0]
        self.assertIn("comprimido, sin", ev["DESCRIPTION"])
        self.assertIn("ejecutables; y", ev["DESCRIPTION"])

    def test_fechas(self):
        from datetime import datetime, timezone
        leida = datetime.fromtimestamp(_fecha("20260315T235900Z"), tz=timezone.utc)
        self.assertEqual(leida.strftime("%Y-%m-%d %H:%M"), "2026-03-15 23:59")
        self.assertIsNotNone(_fecha("20260320T180000"))
        self.assertIsNotNone(_fecha("20260322"))
        self.assertIsNone(_fecha("no es una fecha"))

    def test_sin_zona_se_interpreta_en_hora_local(self):
        """Moodle escribe las fechas sin Z en la hora de la universidad."""
        from datetime import datetime
        from usalbot.config import cfg
        leida = datetime.fromtimestamp(_fecha("20260320T180000"), tz=cfg.tz)
        self.assertEqual(leida.strftime("%H:%M"), "18:00")

    def test_separa_titulo_asignatura_y_tipo(self):
        self.assertEqual(_partir_asunto("Práctica 2 (Redes) se debe entregar"),
                         ("Práctica 2", "Redes", "assign"))
        self.assertEqual(_partir_asunto("Test 6 (Cálculo I) se cierra"),
                         ("Test 6", "Cálculo I", "quiz"))
        self.assertEqual(_partir_asunto("Reunión de delegados"),
                         ("Reunión de delegados", "", "event"))

    def test_el_parentesis_del_titulo_no_estorba(self):
        """Un título con paréntesis propios no debe perderse."""
        t, c, _ = _partir_asunto("Entrega (versión final) (Física General) se debe entregar")
        self.assertEqual(c, "Física General")
        self.assertEqual(t, "Entrega (versión final)")


class TestConversion(unittest.TestCase):
    def setUp(self):
        self.tareas = convertir(CALENDARIO)

    def test_salen_todas(self):
        self.assertEqual(len(self.tareas), 3)

    def test_datos_de_una_entrega(self):
        t = self.tareas[0]
        self.assertEqual(t.title, "Práctica 2: sockets")
        self.assertEqual(t.course, "Redes de Computadores")
        self.assertEqual(t.kind, "assign")
        self.assertTrue(t.id.startswith("studium:ical:"))
        self.assertIsNotNone(t.due)

    def test_categories_manda_sobre_el_parentesis(self):
        self.assertEqual(self.tareas[1].course, "Cálculo I")
        self.assertEqual(self.tareas[1].kind, "quiz")

    def test_enlace_cuando_lo_trae(self):
        self.assertIn("mod/quiz/view.php", self.tareas[1].url)

    def test_ids_estables_entre_descargas(self):
        otra_vez = convertir(CALENDARIO)
        self.assertEqual([t.id for t in self.tareas], [t.id for t in otra_vez])

    def test_asignaturas_deducidas(self):
        nombres = {c.name for c in cursos_de(self.tareas)}
        self.assertEqual(nombres, {"Redes de Computadores", "Cálculo I"})

    def test_calendario_vacio(self):
        self.assertEqual(convertir("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"), [])

    def test_basura_no_revienta(self):
        self.assertEqual(convertir("esto no es un calendario"), [])


if __name__ == "__main__":
    unittest.main()
