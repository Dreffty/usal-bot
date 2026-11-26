"""Comparar texto en castellano sin que las tildes lo estropeen todo.

Uno escribe "calculo" y la asignatura se llama "Cálculo I". Si se compara tal
cual, no hay manera. Aqui esta lo que hace falta para que se encuentren.
"""

from __future__ import annotations

import re
import unicodedata

# Palabras que aparecen en casi todos los nombres de asignatura y no distinguen.
VACIAS = {"de", "del", "la", "el", "los", "las", "y", "e", "en", "a", "al",
          "i", "ii", "iii", "iv", "grado", "curso", "asignatura"}


def sin_tildes(texto: str) -> str:
    """Quita tildes y dieresis, y tambien la ñ (pasa a n), a proposito:
    asi "nunez" encuentra a la profesora Núñez."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def palabras(texto: str) -> list[str]:
    """Las palabras que de verdad distinguen un nombre de otro.

    Los numeros cuentan aunque sean de una cifra: en "Practica 4" o "Tema 5"
    el numero es justo lo que separa una tarea de la siguiente.
    """
    crudas = re.findall(r"[a-z0-9]+", sin_tildes(texto))
    return [p for p in crudas
            if p not in VACIAS and (len(p) > 2 or p.isdigit())]


def parecido(consulta: str, candidato: str) -> float:
    """De 0 a 1: cuanto de lo que has escrito aparece en el nombre real.

    "redes" contra "Redes de Computadores" da 1: todo lo que dijiste esta ahi.
    Se mide sobre la consulta, no sobre el candidato, porque uno escribe el
    nombre a medias y espera que se entienda igual.
    """
    pedidas = palabras(consulta)
    if not pedidas:
        return 0.0
    disponibles = palabras(candidato)
    if not disponibles:
        return 0.0

    aciertos = sum(1 for p in pedidas if any(casan(p, d) for d in disponibles))
    return aciertos / len(pedidas)


def casan(a: str, b: str) -> bool:
    """Si dos palabras son 'la misma' para buscar una asignatura.

    En castellano la misma palabra cambia de forma todo el rato: "fisica" y
    "fisicos", "practica" y "practicas", "logico" y "logicas". Comparar letra
    a letra deja fuera la mitad de los casos, asi que basta con que compartan
    una raiz suficientemente larga.
    """
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    if len(a) < 5 or len(b) < 5:
        return False
    comun = 0
    for x, y in zip(a, b):
        if x != y:
            break
        comun += 1
    return comun >= 5
