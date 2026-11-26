#!/usr/bin/env python3
"""Ensena como responde el bot, con datos inventados y sin conectarse a nada.

    python demo.py

Sirve para ver el aspecto de los mensajes antes de configurar Studium.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

from usalbot import alerts, db, format as fmt, nlu
from usalbot.config import cfg
from usalbot.models import Course, Mail, Task
from usalbot.triage import puntuar_todos

AHORA = int(time.time())
H = 3600


# Nombres como los escribe de verdad la universidad, con su titulación detrás.
ASIGNATURAS = {
    "Cálculo I": 11,
    "Redes de Computadores (Grado en Ingeniería Informática) 2025-26": 12,
    "Programación II": 13,
    "Física General": 14,
    "Historia de la Ciencia y de la Técnica": 15,
}


def tarea(id_, titulo, curso, horas, kind="assign"):
    return Task(id=f"studium:ev:{id_}", kind=kind, title=titulo, course=curso,
                course_id=ASIGNATURAS[curso], due=AHORA + int(horas * H),
                url=f"https://studium.usal.es/mod/{kind}/view.php?id={id_}")


TAREAS = [
    tarea(1, "Boletín 4: integrales", "Cálculo I", -19),
    tarea(2, "Práctica 2: sockets", "Redes de Computadores (Grado en Ingeniería Informática) 2025-26", 5),
    tarea(3, "Test tema 6", "Programación II", 27, kind="quiz"),
    tarea(4, "Memoria de laboratorio", "Física General", 62),
    tarea(5, "Entrega del proyecto", "Programación II", 150),
    tarea(6, "Cuestionario de lecturas", "Historia de la Ciencia y de la Técnica", 300, kind="quiz"),
]

CORREOS = puntuar_todos([
    Mail(id="1", subject="Cambio de aula: el examen del 12/03 será en el aula 204",
         sender="Prof. García", sender_email="profe.ejemplo1@usal.es", unread=True,
         snippet="Buenas tardes, os informo de que el examen parcial cambia de aula.",
         ts=AHORA - 2 * H, url="https://mail.google.com/"),
    Mail(id="2", subject="Studium: Nueva tarea en Redes de Computadores",
         sender="Studium", sender_email="noreply@studium.usal.es",
         snippet="Se ha añadido 'Práctica 2: sockets' con fecha de entrega.",
         ts=AHORA - 5 * H, url="https://mail.google.com/"),
    Mail(id="3", subject="Convocatoria de becas de colaboración 2026",
         sender="Secretaría", sender_email="secretaria@usal.es", unread=True,
         snippet="El plazo termina el 28 de marzo.", ts=AHORA - 26 * H,
         url="https://mail.google.com/"),
    Mail(id="4", subject="Reunión importante",
         sender="Prof. Martín", sender_email="profe.ejemplo2@usal.es", unread=True,
         adjuntos=("normas_entrega.pdf",),
         snippet="Os recuerdo que la entrega de la memoria se amplía hasta el 20 de marzo.",
         ts=AHORA - 28 * H, url="https://mail.google.com/"),
    Mail(id="5", subject="Newsletter: 20% de descuento en material",
         sender="Papelería", sender_email="news@papeleria.com",
         snippet="Para darse de baja pulse aquí.", ts=AHORA - 30 * H),
])

CURSOS = [Course(id=i, name=n, url=f"https://studium.usal.es/course/view.php?id={i}")
          for n, i in ASIGNATURAS.items()]


def a_texto(html: str) -> str:
    """Quita las etiquetas para poder leerlo en la terminal."""
    texto = re.sub(r"<a href=\"[^\"]*\">(.*?)</a>", r"\1", html, flags=re.S)
    texto = re.sub(r"</?(b|i|code|pre)>", "", texto)
    return texto.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def bloque(titulo: str, contenido: str) -> None:
    print(f"\n\033[90m{'─' * 62}\033[0m")
    print(f"\033[96m tú →\033[0m {titulo}")
    print(f"\033[90m{'─' * 62}\033[0m")
    print(a_texto(contenido))


async def main() -> None:
    tmp = tempfile.TemporaryDirectory()
    db.usar_base(Path(tmp.name) / "demo.db")
    db.init()
    db.guardar_tareas(TAREAS)
    db.guardar_correos(CORREOS)
    db.guardar_cursos(CURSOS)

    ahora = datetime.now(cfg.tz)
    print("\n\033[1m  DEMO — así responde el bot (datos inventados)\033[0m")

    bloque("/tareas", fmt.lista_tareas(db.tareas_pendientes(limite=8), "Lo que tienes pendiente"))
    bloque("/hoy", fmt.lista_tareas(
        [t for t in db.tareas_pendientes() if t.due_dt and t.due_dt.date() == ahora.date()],
        "Para hoy", "Hoy no vence nada."))
    bloque("/semana", fmt.agenda_semana(
        db.tareas_entre(AHORA, AHORA + 7 * 24 * H)))
    bloque("/urgente", fmt.lista_tareas(
        [t for t in db.tareas_pendientes() if (t.horas_restantes or 999) < 72],
        "🚨 Esto es lo que corre prisa"))
    bloque("/clave", fmt.lista_correos(db.correos(limite=5, solo_clave=True),
                                       "🔔 Correos que importan", con_motivos=True))
    bloque("/resumen (también llega solo cada mañana)",
           fmt.resumen_diario(db.tareas_pendientes(limite=20), db.correos(limite=10)))

    aviso = alerts.avisos_a_enviar(db.tareas_pendientes())
    if aviso:
        t, tramo = aviso[0]
        bloque("(aviso que llega sin pedirlo)", fmt.aviso_deadline(t, tramo))

    print(f"\n\033[90m{'─' * 62}\033[0m")
    print("\033[1m  Preguntas sueltas: qué entiende sin comandos\033[0m")
    print(f"\033[90m{'─' * 62}\033[0m")
    print("\n  Y del texto libre al nombre real de la asignatura:")
    for texto in ["redes", "calculo", "historia de la ciencia", "química orgánica"]:
        c = db.resolver_curso(texto)
        print(f"    «{texto}»".ljust(34), "→", c.name if c else "\033[90m(no la tienes)\033[0m")
    print()

    for pregunta in ["¿qué tengo esta semana?", "¿qué es lo más urgente?",
                     "¿me he perdido algún correo importante?",
                     "¿cuándo se entrega la práctica de redes?",
                     "¿qué tengo de cálculo?", "¿qué asignaturas tengo?",
                     "ponme al día"]:
        i = await nlu.entender(pregunta)
        extra = f"  · filtro: «{i.filtro}»" if i.filtro else ""
        print(f"  {pregunta:<42} → \033[92m{i.nombre}\033[0m{extra}")

    db.usar_base(cfg.db_path)
    tmp.cleanup()
    print()


if __name__ == "__main__":
    asyncio.run(main())
