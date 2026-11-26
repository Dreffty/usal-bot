"""Base de datos local (SQLite). Guarda lo ya visto para no repetir avisos."""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import cfg
from .models import Course, Mail, Task
from .texto import palabras, parecido, sin_tildes

ESQUEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    course      TEXT DEFAULT '',
    course_id   INTEGER DEFAULT 0,
    url         TEXT DEFAULT '',
    due         INTEGER,
    done        INTEGER DEFAULT 0,
    overdue     INTEGER DEFAULT 0,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    announced   INTEGER DEFAULT 0,
    -- Puesta a 1 por /hecho. Sirve para que la siguiente sincronizacion no
    -- resucite lo que el usuario ha dicho expresamente que ya entrego.
    a_mano      INTEGER DEFAULT 0,
    -- Puesta a 1 cuando desaparece de Studium sin /hecho: entregada o quitada.
    desaparecida INTEGER DEFAULT 0,
    -- Avisos pospuestos hasta este epoch (ver /aplazar). NULL = sin aplazar.
    aplazada_hasta INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due);

CREATE TABLE IF NOT EXISTS mails (
    id           TEXT PRIMARY KEY,
    subject      TEXT NOT NULL,
    sender       TEXT DEFAULT '',
    sender_email TEXT DEFAULT '',
    snippet      TEXT DEFAULT '',
    ts           INTEGER NOT NULL,
    url          TEXT DEFAULT '',
    score        INTEGER DEFAULT 0,
    reasons      TEXT DEFAULT '[]',
    unread       INTEGER DEFAULT 0,
    adjuntos     TEXT DEFAULT '[]',
    resumen      TEXT DEFAULT '',
    notified     INTEGER DEFAULT 0,
    first_seen   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mails_ts ON mails(ts);

CREATE TABLE IF NOT EXISTS courses (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    shortname TEXT DEFAULT '',
    url       TEXT DEFAULT ''
);

-- Un aviso por tarea y por "tramo" (7d, 3d, 24h, 3h), para no repetirse.
CREATE TABLE IF NOT EXISTS reminders (
    task_id TEXT NOT NULL,
    bucket  TEXT NOT NULL,
    sent_at INTEGER NOT NULL,
    PRIMARY KEY (task_id, bucket)
);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


# Normalmente es la del .env; los tests la apuntan a un fichero temporal.
_ruta: Path | None = None


def _migrar(c: sqlite3.Connection) -> None:
    """Columnas anadidas despues, para bases que ya existian."""
    tiene = {f["name"] for f in c.execute("PRAGMA table_info(mails)")}
    if "adjuntos" not in tiene:
        c.execute("ALTER TABLE mails ADD COLUMN adjuntos TEXT DEFAULT '[]'")
    tiene = {f["name"] for f in c.execute("PRAGMA table_info(tasks)")}
    if "a_mano" not in tiene:
        c.execute("ALTER TABLE tasks ADD COLUMN a_mano INTEGER DEFAULT 0")
    if "desaparecida" not in tiene:
        c.execute("ALTER TABLE tasks ADD COLUMN desaparecida INTEGER DEFAULT 0")
    if "aplazada_hasta" not in tiene:
        c.execute("ALTER TABLE tasks ADD COLUMN aplazada_hasta INTEGER")
    tiene = {f["name"] for f in c.execute("PRAGMA table_info(mails)")}
    if "resumen" not in tiene:
        c.execute("ALTER TABLE mails ADD COLUMN resumen TEXT DEFAULT ''")


def usar_base(ruta: Path | str) -> None:
    global _ruta
    _ruta = Path(ruta)


_preparada: Path | None = None


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    global _preparada
    ruta = _ruta or cfg.db_path
    c = sqlite3.connect(ruta, timeout=15)
    c.row_factory = sqlite3.Row
    c.create_function("sin_tildes", 1, sin_tildes, deterministic=True)
    try:
        if _preparada != ruta:
            # Crear las tablas es idempotente: asi nadie tiene que acordarse
            # de llamar a init() antes de usar la base.
            c.executescript(ESQUEMA)
            _migrar(c)
            _preparada = ruta
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn():
        pass


# ----------------------------------------------------------------- tareas
def guardar_tareas(tareas: list[Task]) -> tuple[list[Task], list[tuple[Task, int | None]]]:
    """Guarda o actualiza.

    Devuelve las que son nuevas y las que han cambiado de fecha (con la fecha
    que tenian antes), porque que muevan una entrega es justo lo que interesa
    saber en el momento.
    """
    ahora = int(time.time())
    nuevas: list[Task] = []
    movidas: list[tuple[Task, int | None]] = []

    with conn() as c:
        previas = {r["id"]: r["due"]
                   for r in c.execute("SELECT id, due FROM tasks")}
        for t in tareas:
            if t.id not in previas:
                nuevas.append(t)
            elif previas[t.id] != t.due:
                movidas.append((t, previas[t.id]))
                # Fecha nueva, avisos nuevos: si no, un plazo ampliado y vuelto
                # a acercar no volveria a avisar nunca.
                c.execute("DELETE FROM reminders WHERE task_id = ?", (t.id,))

            c.execute(
                """INSERT INTO tasks (id, kind, title, course, course_id, url, due, done,
                                      overdue, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       title=excluded.title, course=excluded.course, url=excluded.url,
                       due=excluded.due, overdue=excluded.overdue,
                       last_seen=excluded.last_seen,
                       -- Lo que el usuario marco con /hecho se queda hecho:
                       -- si no, volvia a aparecer en la siguiente pasada.
                       done=CASE WHEN tasks.a_mano = 1 THEN 1 ELSE excluded.done END,
                       -- Si vuelve a salir en Studium, ya no esta desaparecida.
                       desaparecida=0""",
                (t.id, t.kind, t.title, t.course, t.course_id, t.url, t.due,
                 int(t.done), int(t.overdue), ahora, ahora),
            )
    return nuevas, movidas


def _como_literal(texto: str) -> str:
    """Prepara un texto para LIKE tratandolo como lo que es: texto.

    En LIKE, "%" significa "lo que sea" y "_" significa "un caracter
    cualquiera". Sin escaparlos, buscar "%" devolvia la lista entera y "_"
    tambien.
    """
    escapado = (sin_tildes(texto).replace("\\", "\\\\")
                .replace("%", "\\%").replace("_", "\\_"))
    return f"%{escapado}%"


def _fila_a_tarea(r: sqlite3.Row) -> Task:
    return Task(id=r["id"], kind=r["kind"], title=r["title"], course=r["course"],
                course_id=r["course_id"], url=r["url"], due=r["due"],
                done=bool(r["done"]), overdue=bool(r["overdue"]),
                desaparecida=bool(r["desaparecida"] or 0),
                aplazada_hasta=r["aplazada_hasta"])


def tareas_pendientes(limite: int = 50, incluir_vencidas: bool = True) -> list[Task]:
    corte = 0 if incluir_vencidas else int(time.time())
    with conn() as c:
        filas = c.execute(
            """SELECT * FROM tasks
               WHERE done = 0 AND (due IS NULL OR due >= ?)
               ORDER BY CASE WHEN due IS NULL THEN 1 ELSE 0 END, due ASC
               LIMIT ?""",
            (corte, limite),
        ).fetchall()
    return [_fila_a_tarea(r) for r in filas]


def tareas_entre(desde: int, hasta: int) -> list[Task]:
    with conn() as c:
        filas = c.execute(
            "SELECT * FROM tasks WHERE done = 0 AND due BETWEEN ? AND ? ORDER BY due ASC",
            (desde, hasta),
        ).fetchall()
    return [_fila_a_tarea(r) for r in filas]


def tareas_de_curso(texto: str, limite: int = 25) -> list[Task]:
    """Busca por asignatura o por nombre de tarea, ignorando tildes.

    Busca por palabras, no por la frase entera: "practica 4" encuentra
    "Práctica 4: Video" aunque no sean el mismo texto seguido. Todas las
    palabras tienen que aparecer, para que no salga media lista.
    """
    # palabras() deja fuera articulos y preposiciones: "la practica 4" busca
    # "practica" y "4", no "la", que estaria en media base de datos.
    trozos = palabras(texto) or [sin_tildes(texto)]

    donde = " AND ".join(
        ["(sin_tildes(course) LIKE ? ESCAPE '\\' "
         "OR sin_tildes(title) LIKE ? ESCAPE '\\')"] * len(trozos))
    # Lo que casa en el nombre de la tarea va antes que lo que solo casa en el
    # de la asignatura: si buscas "practica 4" quieres esa, no todo el curso.
    en_titulo = " AND ".join(
        ["sin_tildes(title) LIKE ? ESCAPE '\\'"] * len(trozos))

    valores: list[object] = []
    for t in trozos:
        patron = _como_literal(t)
        valores += [patron, patron]
    valores += [_como_literal(t) for t in trozos]
    valores.append(limite)

    with conn() as c:
        filas = c.execute(
            f"""SELECT * FROM tasks WHERE done = 0 AND {donde}
                ORDER BY CASE WHEN {en_titulo} THEN 0 ELSE 1 END,
                         CASE WHEN due IS NULL THEN 1 ELSE 0 END, due ASC
                LIMIT ?""",
            valores,
        ).fetchall()
    return [_fila_a_tarea(r) for r in filas]


def tareas_del_curso_id(course_id: int, limite: int = 25) -> list[Task]:
    with conn() as c:
        filas = c.execute(
            """SELECT * FROM tasks WHERE done = 0 AND course_id = ?
               ORDER BY CASE WHEN due IS NULL THEN 1 ELSE 0 END, due ASC LIMIT ?""",
            (course_id, limite),
        ).fetchall()
    return [_fila_a_tarea(r) for r in filas]


def marcar_hecha(task_id: str) -> None:
    """Marca como entregada por decision del usuario (/hecho)."""
    with conn() as c:
        c.execute("UPDATE tasks SET done = 1, a_mano = 1, desaparecida = 0 WHERE id = ?", (task_id,))


def desmarcar_hecha(task_id: str) -> bool:
    """Deshace un /hecho. Devuelve si habia algo que deshacer."""
    with conn() as c:
        cur = c.execute(
            "UPDATE tasks SET done = 0, a_mano = 0, desaparecida = 0 WHERE id = ? AND a_mano = 1",
            (task_id,))
        return cur.rowcount > 0


def tarea_por_id(task_id: str) -> Task | None:
    with conn() as c:
        fila = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _fila_a_tarea(fila) if fila else None


def marcar_desaparecidas_como_hechas(ids_vivos: set[str]) -> int:
    """Lo que Studium deja de listar se oculta (entregada o quitada).

    No se borra: se marca desaparecida para que /entregadas la enseñe y se
    pueda recuperar con /deshacer si fue un error.
    """
    with conn() as c:
        filas = c.execute("SELECT id FROM tasks WHERE done = 0").fetchall()
        fuera = [r["id"] for r in filas if r["id"] not in ids_vivos]
        if fuera:
            c.executemany("UPDATE tasks SET done = 1, desaparecida = 1 WHERE id = ?",
                          [(i,) for i in fuera])
    return len(fuera)


def tareas_ocultas(limite: int = 30) -> list[Task]:
    """Las que desaparecieron de Studium sin /hecho (ver /entregadas)."""
    with conn() as c:
        filas = c.execute(
            "SELECT * FROM tasks WHERE desaparecida = 1 ORDER BY last_seen DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [_fila_a_tarea(r) for r in filas]


def recuperar_oculta(task_id: str) -> bool:
    """Devuelve una tarea oculta a pendientes. Dice si habia algo que traer."""
    with conn() as c:
        cur = c.execute(
            "UPDATE tasks SET done = 0, desaparecida = 0 WHERE id = ? AND desaparecida = 1",
            (task_id,))
        return cur.rowcount > 0


def aplazar(task_id: str, hasta_epoch: int) -> None:
    """Pospone los avisos de una tarea hasta ese momento (ver /aplazar)."""
    with conn() as c:
        c.execute("UPDATE tasks SET aplazada_hasta = ? WHERE id = ?", (hasta_epoch, task_id))
        # Los tramos ya avisados se olvidan: si no, al cumplirse el plazo
        # seguirian marcados y la tarea no reavisaria nunca.
        c.execute("DELETE FROM reminders WHERE task_id = ?", (task_id,))


def quitar_aplazamiento(task_id: str) -> None:
    """Cancela un /aplazar: los avisos vuelven a su ritmo normal."""
    with conn() as c:
        c.execute("UPDATE tasks SET aplazada_hasta = NULL WHERE id = ?", (task_id,))


def purgar_antiguos(dias: int = 90) -> tuple[int, int]:
    """Borra lo viejo para que la base no crezca sin fin.

    Tareas terminadas cuya fecha (o ultima vez vistas) pase de `dias`, y
    correos ya notificados mas viejos que `dias`. Lo no leido se queda.
    Devuelve (tareas_borradas, correos_borrados).
    """
    corte = int(time.time()) - dias * 86400
    # Copia rapida por si la purga se lleva algo que hacia falta.
    try:
        ruta = _ruta or cfg.db_path
        if Path(ruta).exists():
            shutil.copyfile(ruta, str(ruta) + ".bak")
    except OSError:
        pass
    with conn() as c:
        cur = c.execute(
            "DELETE FROM tasks WHERE done = 1 AND COALESCE(due, last_seen) < ?",
            (corte,))
        tareas = cur.rowcount
        cur = c.execute(
            "DELETE FROM mails WHERE notified = 1 AND unread = 0 AND ts < ?",
            (corte,))
        correos = cur.rowcount
        # Los avisos de tareas ya borradas no sirven para nada.
        c.execute("DELETE FROM reminders WHERE task_id NOT IN (SELECT id FROM tasks)")
    return tareas, correos


# ----------------------------------------------------------------- correos
def guardar_correos(correos: list[Mail]) -> list[Mail]:
    ahora = int(time.time())
    nuevos: list[Mail] = []
    with conn() as c:
        conocidos = {r["id"] for r in c.execute("SELECT id FROM mails")}
        for m in correos:
            if m.id not in conocidos:
                nuevos.append(m)
            c.execute(
                """INSERT INTO mails (id, subject, sender, sender_email, snippet, ts, url,
                                      score, reasons, unread, adjuntos, resumen,
                                      first_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       score=excluded.score, reasons=excluded.reasons,
                       unread=excluded.unread, adjuntos=excluded.adjuntos,
                       -- Un resumen ya hecho no se pisa con uno vacio: cuesta
                       -- una llamada al modelo y no cambia entre pasadas.
                       resumen=CASE WHEN excluded.resumen != '' THEN excluded.resumen
                                    ELSE mails.resumen END""",
                (m.id, m.subject, m.sender, m.sender_email, m.snippet, m.ts, m.url,
                 m.score, json.dumps(m.reasons, ensure_ascii=False), int(m.unread),
                 json.dumps(list(m.adjuntos), ensure_ascii=False), m.resumen, ahora),
            )
    return nuevos


def _fila_a_correo(r: sqlite3.Row) -> Mail:
    return Mail(id=r["id"], subject=r["subject"], sender=r["sender"],
                sender_email=r["sender_email"], snippet=r["snippet"], ts=r["ts"],
                url=r["url"], score=r["score"], reasons=json.loads(r["reasons"] or "[]"),
                unread=bool(r["unread"]),
                adjuntos=tuple(json.loads(r["adjuntos"] or "[]")),
                resumen=r["resumen"] or "")


def correos(limite: int = 15, solo_clave: bool = False, desde: int = 0) -> list[Mail]:
    if solo_clave:
        consulta = "SELECT * FROM mails WHERE ts >= ? AND score >= ? ORDER BY ts DESC LIMIT ?"
        valores: tuple[object, ...] = (desde, cfg.score_clave, limite)
    else:
        consulta = "SELECT * FROM mails WHERE ts >= ? ORDER BY ts DESC LIMIT ?"
        valores = (desde, limite)
    with conn() as c:
        filas = c.execute(consulta, valores).fetchall()
    return [_fila_a_correo(r) for r in filas]


def buscar_correos(texto: str, limite: int = 10) -> list[Mail]:
    like = _como_literal(texto)
    with conn() as c:
        filas = c.execute(
            """SELECT * FROM mails
               WHERE sin_tildes(subject) LIKE ? ESCAPE '\\'
                  OR sin_tildes(sender) LIKE ? ESCAPE '\\'
                  OR sin_tildes(snippet) LIKE ? ESCAPE '\\'
               ORDER BY ts DESC LIMIT ?""",
            (like, like, like, limite),
        ).fetchall()
    return [_fila_a_correo(r) for r in filas]


def guardar_resumen(mail_id: str, texto: str) -> None:
    with conn() as c:
        c.execute("UPDATE mails SET resumen = ? WHERE id = ?", (texto, mail_id))


def correos_clave_sin_resumen(limite: int = 6) -> list[Mail]:
    """Los importantes a los que aún no se les ha sacado un resumen."""
    with conn() as c:
        filas = c.execute(
            "SELECT * FROM mails WHERE score >= ? AND (resumen IS NULL OR resumen = '') "
            "ORDER BY ts DESC LIMIT ?",
            (cfg.score_clave, limite),
        ).fetchall()
    return [_fila_a_correo(r) for r in filas]


def marcar_correos_notificados(ids: list[str]) -> None:
    with conn() as c:
        c.executemany("UPDATE mails SET notified = 1 WHERE id = ?", [(i,) for i in ids])


def correos_clave_sin_notificar(desde: int) -> list[Mail]:
    with conn() as c:
        filas = c.execute(
            "SELECT * FROM mails WHERE notified = 0 AND score >= ? AND ts >= ? "
            "ORDER BY ts DESC",
            (cfg.score_clave, desde),
        ).fetchall()
    return [_fila_a_correo(r) for r in filas]


# ----------------------------------------------------------------- cursos
def guardar_cursos(cursos: list[Course]) -> None:
    with conn() as c:
        c.executemany(
            """INSERT INTO courses (id, name, shortname, url) VALUES (?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                   shortname=excluded.shortname, url=excluded.url""",
            [(x.id, x.name, x.shortname, x.url) for x in cursos],
        )


def resolver_curso(texto: str, minimo: float = 0.5) -> Course | None:
    """De lo que escribes al curso real.

    Compara contra los nombres que Studium tiene guardados, asi que funciona
    con el nombre a medias ("redes") y con el largo y con parentesis que usa
    la universidad ("Redes de Computadores (Grado en...) 2025-26").
    """
    # El parentesis del nombre oficial lleva la titulacion ("(Grado en
    # Ingenieria Informatica) 2025-26"), que es igual en media carrera y solo
    # sirve para confundir: se compara contra el nombre de la asignatura.
    lista = cursos()
    candidatos = [(parecido(texto, c.name.split("(")[0]), c) for c in lista]
    candidatos += [(parecido(texto, c.shortname), c) for c in lista if c.shortname]
    if not candidatos:
        return None
    mejor_nota, mejor = max(candidatos, key=lambda x: x[0])
    return mejor if mejor_nota >= minimo else None


def cursos() -> list[Course]:
    with conn() as c:
        filas = c.execute("SELECT * FROM courses ORDER BY name").fetchall()
    return [Course(id=r["id"], name=r["name"], shortname=r["shortname"], url=r["url"]) for r in filas]


# ----------------------------------------------------------------- avisos
def aviso_pendiente(task_id: str, bucket: str) -> bool:
    with conn() as c:
        fila = c.execute(
            "SELECT 1 FROM reminders WHERE task_id = ? AND bucket = ?", (task_id, bucket)
        ).fetchone()
    return fila is None


def marcar_aviso(task_id: str, bucket: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO reminders (task_id, bucket, sent_at) VALUES (?,?,?)",
            (task_id, bucket, int(time.time())),
        )


# ----------------------------------------------------------------- kv
def get(k: str, por_defecto: str = "") -> str:
    with conn() as c:
        fila = c.execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
    return fila["v"] if fila else por_defecto


def put(k: str, v: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO kv (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
