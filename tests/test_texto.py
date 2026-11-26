"""Que las tildes no impidan encontrar una asignatura."""

import tempfile
import unittest
from pathlib import Path

from usalbot import db
from usalbot.config import cfg
from usalbot.models import Course, Task
from usalbot.texto import palabras, parecido, sin_tildes

CURSOS = [
    Course(id=1, name="Cálculo I", shortname="CAL1"),
    Course(id=2, name="Redes de Computadores (Grado en Ingeniería Informática) 2025-26",
           shortname="RC"),
    Course(id=3, name="Física General", shortname="FIS"),
    Course(id=4, name="Fundamentos de Programación (Grado en Ingeniería Informática)",
           shortname="FP"),
    Course(id=5, name="Historia de la Ciencia y de la Técnica", shortname="HCT"),
]


class TestTexto(unittest.TestCase):
    def test_sin_tildes(self):
        self.assertEqual(sin_tildes("Cálculo Ingeniería"), "calculo ingenieria")

    def test_la_enye_tambien_se_normaliza(self):
        """A propósito: así "nunez" encuentra a "Núñez"."""
        self.assertEqual(sin_tildes("Núñez"), "nunez")

    def test_palabras_quita_las_vacias(self):
        self.assertEqual(palabras("Historia de la Ciencia I"), ["historia", "ciencia"])

    def test_parecido_por_partes(self):
        self.assertEqual(parecido("redes", "Redes de Computadores"), 1.0)
        self.assertEqual(parecido("redes de computadores", "Redes de Computadores"), 1.0)
        self.assertEqual(parecido("calculo", "Cálculo I"), 1.0)
        self.assertEqual(parecido("program", "Programación"), 1.0)
        self.assertEqual(parecido("quimica", "Cálculo I"), 0.0)

    def test_parecido_sin_texto(self):
        self.assertEqual(parecido("", "Cálculo"), 0.0)
        self.assertEqual(parecido("de la", "Cálculo"), 0.0)


class TestBusquedaEnBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()
        db.guardar_cursos(CURSOS)
        db.guardar_tareas([
            Task(id="a", kind="assign", title="Boletín 4", course="Cálculo I",
                 course_id=1, due=2_000_000_000),
            Task(id="b", kind="assign", title="Práctica 2", course=CURSOS[1].name,
                 course_id=2, due=2_000_000_000),
        ])

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    def test_buscar_sin_tildes_encuentra(self):
        """El fallo que hacía inútil preguntar por media carrera."""
        self.assertEqual(len(db.tareas_de_curso("calculo")), 1)
        self.assertEqual(len(db.tareas_de_curso("cálculo")), 1)
        self.assertEqual(len(db.tareas_de_curso("boletin")), 1)
        self.assertEqual(len(db.tareas_de_curso("Boletín")), 1)

    def test_resolver_nombre_a_medias(self):
        self.assertEqual(db.resolver_curso("redes").id, 2)
        self.assertEqual(db.resolver_curso("calculo").id, 1)
        self.assertEqual(db.resolver_curso("fisica").id, 3)
        self.assertEqual(db.resolver_curso("historia de la ciencia").id, 5)

    def test_la_titulacion_no_confunde(self):
        """'informatica' sale en el paréntesis de media carrera: no debe valer."""
        self.assertIsNone(db.resolver_curso("informatica"))
        self.assertIsNone(db.resolver_curso("grado"))

    def test_asignatura_inexistente(self):
        self.assertIsNone(db.resolver_curso("química orgánica"))

    def test_tareas_por_id_de_curso(self):
        self.assertEqual(len(db.tareas_del_curso_id(2)), 1)
        self.assertEqual(len(db.tareas_del_curso_id(99)), 0)

    def test_buscar_correos_sin_tildes(self):
        from usalbot.models import Mail
        db.guardar_correos([Mail(id="m", subject="Reunión de prácticas",
                                 sender="Profesora Núñez", ts=1)])
        self.assertEqual(len(db.buscar_correos("reunion")), 1)
        self.assertEqual(len(db.buscar_correos("nunez")), 1)


if __name__ == "__main__":
    unittest.main()


class TestBusquedaPorPalabras(unittest.TestCase):
    """Buscar la frase entera fallaba: "la práctica 4" no aparece tal cual en
    ningún título. Y los números de una cifra son justo lo que distingue."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()
        db.guardar_tareas([
            Task(id="1", kind="assign", title="Práctica 4: Video",
                 course="Fundamentos Físicos", course_id=1),
            Task(id="2", kind="assign", title="Práctica 2: Sockets",
                 course="Fundamentos Físicos", course_id=1, due=2_000_000_000),
            Task(id="3", kind="quiz", title="Test Autoevaluación - Tema 5",
                 course="Computadores I", course_id=2, due=2_000_000_100),
            Task(id="4", kind="assign",
                 title="Solicitud de cambio de subgrupo prácticas",
                 course="Coordinación del Grado", course_id=3, due=1_900_000_000),
        ])

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    def test_el_numero_distingue(self):
        r = db.tareas_de_curso("practica 4")
        self.assertEqual([t.title for t in r], ["Práctica 4: Video"])

    def test_los_articulos_no_estorban(self):
        self.assertEqual(db.tareas_de_curso("la practica 4")[0].title,
                         "Práctica 4: Video")

    def test_palabras_sueltas_en_cualquier_orden(self):
        self.assertEqual(db.tareas_de_curso("video practica")[0].title,
                         "Práctica 4: Video")

    def test_el_titulo_manda_sobre_la_asignatura(self):
        """Buscando 'tema 5' se quiere esa tarea, no todo Computadores I."""
        self.assertEqual(db.tareas_de_curso("tema 5")[0].title,
                         "Test Autoevaluación - Tema 5")

    def test_una_palabra_sola_trae_varias(self):
        self.assertEqual(len(db.tareas_de_curso("practica")), 3)

    def test_lo_que_no_existe_no_aparece(self):
        self.assertEqual(db.tareas_de_curso("practica 9"), [])

    def test_numeros_en_palabras(self):
        from usalbot.texto import palabras
        self.assertEqual(palabras("Práctica 4"), ["practica", "4"])
        self.assertEqual(palabras("Tema 5. Familias lógicas"),
                         ["tema", "5", "familias", "logicas"])


class TestVariantesDelCastellano(unittest.TestCase):
    """La misma palabra cambia de forma continuamente: "física"/"físicos",
    "práctica"/"prácticas". Comparar letra a letra dejaba fuera media lista."""

    def test_genero_y_numero(self):
        from usalbot.texto import casan
        for a, b in [("fisica", "fisicos"), ("practica", "practicas"),
                     ("logico", "logicas"), ("informatica", "informatico"),
                     ("computador", "computadores")]:
            self.assertTrue(casan(a, b), f"{a} debería casar con {b}")

    def test_palabras_distintas_no_casan(self):
        from usalbot.texto import casan
        for a, b in [("fisica", "filosofia"), ("algebra", "algoritmo"),
                     ("quimica", "cinematica"), ("redes", "reglas")]:
            self.assertFalse(casan(a, b), f"{a} NO debería casar con {b}")

    def test_palabras_cortas_exigen_prefijo(self):
        from usalbot.texto import casan
        self.assertTrue(casan("red", "redes"))
        self.assertFalse(casan("casa", "caso"))

    def test_asignatura_por_una_palabra_en_otra_forma(self):
        cursos = [Course(id=1, name="FUNDAMENTOS FÍSICOS (Grado en Ing. Informática)"),
                  Course(id=2, name="Algebra lineal y Geometría 2026-2027")]
        db.usar_base(Path(tempfile.mkdtemp()) / "t.db")
        db.init()
        db.guardar_cursos(cursos)
        try:
            self.assertEqual(db.resolver_curso("fisica").id, 1)
            self.assertEqual(db.resolver_curso("fisicos").id, 1)
            self.assertEqual(db.resolver_curso("algebra").id, 2)
            self.assertIsNone(db.resolver_curso("quimica"))
        finally:
            db.usar_base(cfg.db_path)


class TestComodinesDeSQL(unittest.TestCase):
    """En LIKE, "%" significa "lo que sea" y "_" "un carácter cualquiera".
    Sin escaparlos, buscar "%" devolvía la lista entera."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.usar_base(Path(self.tmp.name) / "t.db")
        db.init()
        db.guardar_tareas([
            Task(id="1", kind="assign", title="Examen 100% teoría",
                 course="Física", due=2_000_000_000),
            Task(id="2", kind="assign", title="Tema_5 repaso",
                 course="Física", due=2_000_000_000),
            Task(id="3", kind="assign", title="Nada que ver",
                 course="Redes", due=2_000_000_000),
        ])

    def tearDown(self):
        db.usar_base(cfg.db_path)
        self.tmp.cleanup()

    def test_el_porcentaje_es_un_porcentaje(self):
        r = db.tareas_de_curso("%")
        self.assertEqual([t.title for t in r], ["Examen 100% teoría"])

    def test_el_guion_bajo_es_un_guion_bajo(self):
        r = db.tareas_de_curso("_")
        self.assertEqual([t.title for t in r], ["Tema_5 repaso"])

    def test_lo_que_no_existe_sigue_sin_existir(self):
        self.assertEqual(db.tareas_de_curso("___"), [])

    def test_busqueda_normal_intacta(self):
        self.assertEqual(len(db.tareas_de_curso("teoria")), 1)

    def test_correos_tambien(self):
        from usalbot.models import Mail
        db.guardar_correos([Mail(id="m", subject="Subida del 50% en tasas", ts=1),
                            Mail(id="n", subject="Otra cosa", ts=2)])
        self.assertEqual(len(db.buscar_correos("%")), 1)
