"""Cuando avisar de una entrega y cuando callarse.

Cuatro toques por tarea como mucho: una semana, tres dias, un dia y tres
horas antes. Cada uno se manda una sola vez en la vida de esa tarea.
Si una tarea vence sin que la hayas visto (por ejemplo, el bot estuvo
parado), se avisa una vez como vencida en vez de callarse para siempre.
Las tareas aplazadas con /aplazar no avisan hasta que pasa su fecha.
"""

from __future__ import annotations

import time

from .db import aviso_pendiente, marcar_aviso
from .models import Task

# (etiqueta, desde_horas, hasta_horas) - la tarea cae en el primero que encaje.
TRAMOS = [
    ("3h", 0, 3),
    ("24h", 3, 24),
    ("3d", 24, 72),
    ("7d", 72, 168),
]


def tramo_de(t: Task) -> str | None:
    h = t.horas_restantes
    if h is None or h < 0:
        return None
    for nombre, desde, hasta in TRAMOS:
        if desde <= h < hasta:
            return nombre
    return None


def avisos_a_enviar(tareas: list[Task]) -> list[tuple[Task, str]]:
    """Las tareas que han entrado en un tramo del que aun no se aviso."""
    ahora = time.time()
    salida: list[tuple[Task, str]] = []
    for t in tareas:
        if t.done:
            continue
        if t.aplazada_hasta and t.aplazada_hasta > ahora:
            continue
        h = t.horas_restantes
        # Vencio hace poco y nunca se aviso: un toque, no silencio.
        # (tramo_de sigue devolviendo None en negativos: sus tests lo fijan.)
        if h is not None and -48 <= h < 0:
            if aviso_pendiente(t.id, "vencida"):
                salida.append((t, "vencida"))
            continue
        tramo = tramo_de(t)
        if tramo and aviso_pendiente(t.id, tramo):
            salida.append((t, tramo))
    return salida


def confirmar(t: Task, tramo: str) -> None:
    marcar_aviso(t.id, tramo)
