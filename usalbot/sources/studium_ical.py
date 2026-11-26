"""Cuando la universidad no deja crear claves de API: el calendario exportado.

Studium permite exportar el calendario en formato iCal (una URL). Esa lista
trae entregas con fecha y curso, que es justo lo que necesita el bot. Lo que
NO trae: saber si ya entregaste algo. Por eso el bot avisa de todo lo que ve
aqui hasta que marcas /hecho o pasa la fecha.

La URL caduca de vez en cuando: si falla, el /estado lo dice y hay que volver
a "Exportar calendario" y pegar la nueva con `setup_wizard.py studium`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from ..config import cfg
from ..models import Course, Task

log = logging.getLogger("usalbot.ical")


class IcalError(Exception):
    """El calendario no se pudo descargar o no vale."""


def _desescapar(s: str) -> str:
    """Deshace los escapes del iCal (\\, \\; \\n \\\\ etc.)."""
    ph = "\x00"
    s = s.replace("\\\\", ph)
    s = s.replace("\\,", ",").replace("\\;", ";")
    s = s.replace("\\n", "\n").replace("\\N", "\n")
    return s.replace(ph, "\\")


def _eventos(texto: str) -> list[dict[str, str]]:
    """Cada VEVENT como diccionario.

    Si un campo trae parametros (p. ej. ``DTSTART;TZID=Europe/Madrid:...``),
    se guarda el valor bajo el nombre base y el TZID bajo ``<NOMBRE>_TZID``.
    Los valores vienen desescapados.
    """
    eventos: list[dict[str, str]] = []
    actual = ""
    dentro = False
    evento: dict[str, str] = {}
    for linea in texto.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if linea[:1] in (" ", "\t"):
            actual += linea[1:]
            continue
        nombre, _, valor = actual.partition(":")
        if nombre == "BEGIN" and valor == "VEVENT":
            dentro, evento = True, {}
        elif nombre == "END" and valor == "VEVENT":
            dentro = False
            eventos.append(evento)
        elif dentro and nombre:
            base, _, params = nombre.partition(";")
            evento[base] = _desescapar(valor)
            m = re.search(r"TZID=([^;:]+)", params)
            if m and base in ("DTSTART", "DUE", "DTEND"):
                evento[f"{base}_TZID"] = m.group(1)
        actual = linea
    return eventos


def _fecha(valor: str, tzid: str | None = None) -> int | None:
    """Un DTSTART/DUE del iCal a epoch.

    Sin hora (``20260315``) = ese dia en tu zona. Con ``Z`` = UTC. Con TZID
    (p. ej. ``Europe/Madrid``) se respeta; si viene algo raro se usa tu zona.
    Devuelve None si no parece una fecha.
    """
    if not valor:
        return None
    valor = valor.strip()
    if re.fullmatch(r"\d{8}", valor):
        dt = datetime.strptime(valor, "%Y%m%d").replace(tzinfo=cfg.tz)
        return int(dt.timestamp())
    m = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", valor)
    if not m:
        return None
    base = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    if m.group(3):
        return int(base.replace(tzinfo=ZoneInfo("UTC")).timestamp())
    if tzid:
        try:
            return int(base.replace(tzinfo=ZoneInfo(tzid)).timestamp())
        except Exception:
            log.debug("TZID desconocida en el iCal: %s", tzid)
    return int(base.replace(tzinfo=cfg.tz).timestamp())


_RE_SUFFIX = re.compile(
    r"\s+(se\s+debe\s+entregar|debe\s+entregarse|hay\s+que\s+entregar|"
    r"se\s+cierra|fecha\s+l[ií]mite|vencimiento)\s*$",
    re.IGNORECASE,
)
_RE_CURSO_FINAL = re.compile(r"\(([^()]+)\)\s*$")


def _partir_asunto(asunto: str) -> tuple[str, str, str]:
    """Titulo, asignatura y tipo desde el SUMMARY de Moodle.

    ``"Práctica 2 (Redes) se debe entregar"`` -> ``("Práctica 2", "Redes", "assign")``.
    El paréntesis final es la asignatura; si el título trae los suyos
    (``"Entrega (versión final) (Física) ..."``) se respeta el último.
    """
    texto = (asunto or "").strip()
    kind = "event"
    bajo = texto.lower()
    if "entreg" in bajo:
        kind = "assign"
    elif "cierra" in bajo or "cierre" in bajo or "quiz" in bajo or "test" in bajo:
        kind = "quiz"
    texto = _RE_SUFFIX.sub("", texto).strip()
    m = _RE_CURSO_FINAL.search(texto)
    if m:
        return texto[: m.start()].strip(), m.group(1).strip(), kind
    return texto, "", kind


def _id_curso(nombre: str) -> int:
    """Id negativo estable: depende del nombre, no del orden de llegada.

    Antes se numeraba por orden alfabetico (-1, -2...), asi que si aparecia
    una asignatura nueva todos los ids cambiaban y el conteo por curso del
    /estado se volvia loco. Con hash el id de cada curso es siempre el mismo.
    """
    digest = hashlib.sha1(nombre.encode("utf-8")).hexdigest()[:8]
    return -(int(digest, 16) % 100_000_000) - 1


def _id_tarea(uid: str, titulo: str, curso: str, due: int | None) -> str:
    """Id estable entre descargas: del UID si lo hay, si no del contenido."""
    base = uid.strip() if uid.strip() else f"{titulo}|{curso}|{due or ''}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"studium:ical:{digest}"


def convertir(texto: str, curso_por_defecto: str = "") -> list[Task]:
    """El texto del .ics a la lista de tareas que entiende el bot."""
    if "BEGIN:VEVENT" not in texto:
        return []
    tareas: list[Task] = []
    for ev in _eventos(texto):
        titulo, curso, kind = _partir_asunto(ev.get("SUMMARY", "(sin titulo)"))
        curso = (ev.get("CATEGORIES", "") or "").strip() or curso or curso_por_defecto
        uid = ev.get("UID", "")
        venc = _fecha(ev.get("DTSTART", ""), ev.get("DTSTART_TZID")) or _fecha(
            ev.get("DUE", ""), ev.get("DUE_TZID")
        )
        # Sin fecha también vale: sync.py la trata como inventario sin fecha.
        if not venc:
            venc = _fecha(ev.get("DTEND", ""), ev.get("DTEND_TZID"))
        tareas.append(
            Task(
                id=_id_tarea(uid, titulo, curso, venc),
                kind=kind,
                title=titulo or "(sin titulo)",
                course=curso,
                course_id=_id_curso(curso) if curso else 0,
                url=ev.get("URL", ""),
                due=venc,
            )
        )
    return tareas


def cursos_de(tareas: list[Task]) -> list[Course]:
    """Solo los cursos con nombre, con ids estables (para guardar en la base)."""
    vistos: dict[str, Course] = {}
    for t in tareas:
        nombre = (t.course or "").strip()
        if nombre and nombre not in vistos:
            vistos[nombre] = Course(id=_id_curso(nombre), name=nombre)
    return sorted(vistos.values(), key=lambda c: c.name)


def anadir_fuente() -> str:
    return "ical"


async def pendientes(url: str) -> list[Task]:
    """Descarga el calendario y lo convierte a tareas."""
    try:
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.get(url)
            r.raise_for_status()
    except Exception as e:
        raise IcalError(f"no he podido descargar el calendario ({e})")
    return convertir(r.text)


async def comprobar(url: str) -> tuple[int, int]:
    """Para el /estado: cuantos eventos y asignaturas trae el calendario."""
    tareas = await pendientes(url)
    return len(tareas), len(cursos_de(tareas))
