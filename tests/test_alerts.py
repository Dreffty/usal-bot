"""Que avise cuatro veces y no cuarenta."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from usalbot import db
from usalbot.config import cfg


def tarea(horas):
    from usalbot.models import Task
    venc = datetime.now(cfg.tz) + timedelta(hours=horas)
    return Task(id=f"t{horas}", kind="assign", title="Entrega", due=int(venc.timestamp()))


class TestAvisos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    def test_tramos(self):
        from usalbot.alerts import tramo_de
        self.assertEqual(tramo_de(tarea(1)), "3h")
        self.assertEqual(tramo_de(tarea(10)), "24h")
        self.assertEqual(tramo_de(tarea(50)), "3d")
        self.assertEqual(tramo_de(tarea(100)), "7d")
        self.assertIsNone(tramo_de(tarea(400)))
        self.assertIsNone(tramo_de(tarea(-5)))

    def test_no_repite_el_mismo_aviso(self):
        from usalbot.alerts import avisos_a_enviar, confirmar
        t = tarea(10)
        primero = avisos_a_enviar([t])
        self.assertEqual(len(primero), 1)
        confirmar(*primero[0])
        self.assertEqual(avisos_a_enviar([t]), [])

    def test_entregada_no_avisa(self):
        from usalbot.alerts import avisos_a_enviar
        t = tarea(10)
        t.done = True
        self.assertEqual(avisos_a_enviar([t]), [])


if __name__ == "__main__":
    unittest.main()
