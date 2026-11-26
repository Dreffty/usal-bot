"""Decide que correos son importantes y por que.

No hace falta leer los 40 correos de la semana: casi todos son ruido. Esto
puntua cada uno con reglas sencillas y explica su decision, para que puedas
discutirsela al bot cuando se equivoque.
"""

from __future__ import annotations

import re
from datetime import date

from .models import Mail
from .texto import sin_tildes

# Palabras que casi siempre significan "esto te afecta y tiene fecha".
FUERTES = {
    "examen": "habla de un examen", "examenes": "habla de examenes",
    "convocatoria": "es una convocatoria", "acta": "menciona actas",
    "calificacion": "trae calificaciones", "calificaciones": "trae calificaciones",
    "nota": "habla de notas", "notas": "habla de notas",
    "entrega": "habla de una entrega", "plazo": "menciona un plazo",
    "fecha limite": "menciona una fecha limite", "matricula": "es de matricula",
    "beca": "es de becas", "tfg": "es del TFG", "tfm": "es del TFM",
    "defensa": "habla de una defensa", "tribunal": "menciona un tribunal",
    "practica": "es de practicas", "practicas": "es de practicas",
    "laboratorio": "es del laboratorio", "tutoria": "es de tutorias",
    "revision": "es una revision", "recuperacion": "es de recuperacion",
    "obligatorio": "dice obligatorio", "urgente": "dice urgente",
    "importante": "marcado como importante", "aviso": "es un aviso",
    "cancelada": "anuncia una cancelacion", "cancelado": "anuncia una cancelacion",
    "aplazada": "anuncia un aplazamiento", "aplazado": "anuncia un aplazamiento",
    "cambio de aula": "cambia el aula", "asistencia": "habla de asistencia",
    "seminario": "es de un seminario", "prueba": "habla de una prueba",
    "correccion": "es una correccion", "rectificacion": "es una rectificacion",
}

# De entre las fuertes, las que casi nunca son un simple "os informo".
DECISIVAS = {"examen", "examenes", "convocatoria", "acta", "matricula", "plazo",
             "fecha limite", "entrega", "defensa", "tribunal", "calificacion",
             "calificaciones", "cancelada", "cancelado", "aplazada", "aplazado",
             "recuperacion", "cambio de aula"}

# Palabras que delatan un correo masivo que no requiere nada de ti.
RUIDO = {
    "newsletter": "parece una newsletter", "boletin": "parece un boletin",
    "promocion": "es promocional", "descuento": "es promocional",
    "webinar": "es publicidad de un webinar", "encuesta de satisfaccion": "es una encuesta",
    "no responda a este": "es un automatico masivo",
    "darse de baja": "trae enlace de baja", "unsubscribe": "trae enlace de baja",
}

DOMINIOS_USAL = ("usal.es",)
REMITENTES_AUTOMATICOS = ("noreply", "no-reply", "nepasrepondre", "mailer-daemon", "notificaciones")

# Fechas escritas a mano dentro del texto: "12/03", "12 de marzo", "antes del 5"
RE_FECHAS = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
    r"|\d{1,2}\s+de\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|setiembre|octubre|noviembre|diciembre))\b",
    re.IGNORECASE,
)


def puntuar(m: Mail) -> Mail:
    """Rellena score y reasons del correo y lo devuelve."""
    asunto = sin_tildes(m.subject)
    # Solo el principio del cuerpo: mas abajo estan las firmas, los pies de
    # pagina y los hilos citados, que disparan falsos positivos.
    cuerpo = sin_tildes(m.snippet[:600])
    texto = f"{asunto} {cuerpo}"
    correo = m.sender_email.lower()
    dominio = correo.split("@")[-1] if "@" in correo else ""

    puntos = 0
    razones: list[str] = []

    es_usal = any(dominio == d or dominio.endswith("." + d) for d in DOMINIOS_USAL)
    automatico = any(p in correo for p in REMITENTES_AUTOMATICOS)

    if es_usal and not automatico:
        puntos += 3
        razones.append("viene de una persona de la USAL")
    elif es_usal:
        puntos += 2
        razones.append("viene de la USAL")

    if "studium" in correo or "moodle" in correo:
        # Que venga por Studium dice de donde sale, no que corra prisa: la
        # mayoria son avisos de foro. Cuenta poco a proposito.
        puntos += 1
        razones.append("es de Studium")

    vistas: set[str] = set()
    # Las de peso llevan doble: son las que de verdad traen una fecha o un
    # tramite que te afecta.
    for palabra, motivo in FUERTES.items():
        if palabra in texto and motivo not in vistas:
            puntos += 3 if palabra in DECISIVAS else 2
            vistas.add(motivo)
            razones.append(motivo)
        if len(vistas) >= 3:
            break

    for palabra, motivo in RUIDO.items():
        if palabra in texto:
            puntos -= 3
            razones.append(motivo)
            break

    if m.unread:
        puntos += 1

    # Un adjunto de la universidad suele ser el enunciado, las normas o el
    # impreso que hay que rellenar. No decide por si solo, pero cuenta.
    if m.adjuntos and es_usal:
        puntos += 1
        razones.append(f"trae {len(m.adjuntos)} adjunto(s)")

    if fechas_mencionadas(m):
        puntos += 1
        razones.append("menciona una fecha")

    m.score = max(puntos, 0)
    m.reasons = razones[:4]
    return m


MESES_NOMBRE = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
                "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
                "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


def _es_fecha_util(bruto: str, hoy: date) -> bool:
    """Descarta lo que no es una fecha y lo que ya ha pasado.

    "2026-27" es un curso academico, no un dia, y avisar de una fecha que fue
    ayer no le sirve a nadie.
    """
    texto = sin_tildes(bruto)
    m = re.match(r"^(\d{1,2})\s+de\s+([a-z]+)$", texto)
    if m:
        dia, mes = int(m.group(1)), MESES_NOMBRE.get(m.group(2), 0)
    else:
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?$", texto)
        if not m:
            return False
        dia, mes = int(m.group(1)), int(m.group(2))

    if not (1 <= dia <= 31 and 1 <= mes <= 12):
        return False

    try:
        este_ano = date(hoy.year, mes, dia)
    except ValueError:
        return False        # 31 de febrero y companyia

    if este_ano >= hoy:
        return True
    # Ya paso este ano. Si fue hace poco, el correo hablaba de algo pasado y
    # no hay nada que recordar; si fue hace meses, se refiere al ano que viene
    # ("20 de marzo" escrito en septiembre es marzo del curso que entra).
    return (hoy - este_ano).days > 60


def fechas_mencionadas(m: Mail) -> list[str]:
    hoy = date.today()
    fuera: list[str] = []
    for f in RE_FECHAS.findall(f"{m.subject} {m.snippet}"):
        if f not in fuera and _es_fecha_util(f, hoy):
            fuera.append(f)
    return fuera[:3]


def puntuar_todos(correos: list[Mail]) -> list[Mail]:
    return [puntuar(m) for m in correos]
