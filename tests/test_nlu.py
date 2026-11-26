"""Las preguntas de verdad que uno escribe a las 2 de la manana."""

import asyncio
import unittest
from unittest.mock import patch

from usalbot.nlu import entender, por_reglas


async def _sin_modelo(_pregunta):
    return None


class TestReglas(unittest.TestCase):
    def _intent(self, pregunta: str) -> str:
        r = por_reglas(pregunta)
        self.assertIsNotNone(r, f"no entendio: {pregunta}")
        return r.nombre

    def test_proximas(self):
        for p in ["que tengo pendiente", "mis tareas", "que entregas me quedan",
                  "proximas entregas", "que deberes tengo"]:
            self.assertEqual(self._intent(p), "proximas", p)

    def test_hoy(self):
        for p in ["que tengo hoy", "algo para hoy?", "que hay esta tarde"]:
            self.assertEqual(self._intent(p), "hoy", p)

    def test_semana(self):
        for p in ["que tengo esta semana", "agenda de la semana", "los proximos 7 dias"]:
            self.assertEqual(self._intent(p), "semana", p)

    def test_urgente(self):
        for p in ["que es lo mas urgente", "que hago primero", "que corre prisa"]:
            self.assertEqual(self._intent(p), "urgente", p)

    def test_correos(self):
        self.assertEqual(self._intent("tengo correos nuevos?"), "correos")
        self.assertEqual(self._intent("algun correo importante?"), "correos_clave")
        self.assertEqual(self._intent("me he perdido algo?"), "correos_clave")

    def test_asignaturas(self):
        self.assertEqual(self._intent("que asignaturas tengo"), "asignaturas")

    def test_filtro_de_asignatura(self):
        r = por_reglas("que tengo de calculo")
        self.assertEqual(r.nombre, "asignatura")
        self.assertEqual(r.filtro, "calculo")

    def test_cuando_con_tema(self):
        r = por_reglas("cuando se entrega la practica de redes")
        self.assertEqual(r.nombre, "cuando")
        self.assertIn("redes", r.filtro)

    def test_dias_explicitos(self):
        r = por_reglas("que tengo en 3 dias")
        self.assertEqual(r.dias, 3)

    def test_otras_formas_de_preguntar(self):
        for p in ["que hay de historia de la ciencia", "que me queda de redes",
                  "que falta por entregar"]:
            self.assertIsNotNone(por_reglas(p), p)

    def test_nombre_largo_de_asignatura(self):
        r = por_reglas("que tengo de redes de computadores")
        self.assertEqual(r.nombre, "asignatura")
        self.assertEqual(r.filtro, "redes de computadores")

    def test_vacio(self):
        self.assertIsNone(por_reglas(""))

    def test_tarea_con_fecha_proxima_es_pendientes(self):
        # "búscame la tarea con la fecha más próxima" iba a la asignatura
        # "fecha mas proxima" en vez de a pendientes.
        r = por_reglas("búscame la tarea con la fecha más próxima")
        self.assertIsNotNone(r)
        self.assertEqual(r.nombre, "proximas")

    def test_tareas_de_curso_sigue_yendo_a_asignatura(self):
        r = por_reglas("qué tengo de cálculo")
        self.assertEqual(r.nombre, "asignatura")
        self.assertEqual(r.filtro, "calculo")

    def test_lo_desconocido_es_duda_abierta_no_tareas(self):
        # "dime bibliotecas cercanas a mi facultad" devolvía pendientes.
        self.assertIsNone(
            por_reglas("dime bibliotecas cercanas a mi facultad"))
        with patch("usalbot.nlu.por_modelo", _sin_modelo):
            inten = asyncio.run(entender(
                "dime bibliotecas cercanas a mi facultad"))
        self.assertEqual(inten.nombre, "pregunta")


if __name__ == "__main__":
    unittest.main()
