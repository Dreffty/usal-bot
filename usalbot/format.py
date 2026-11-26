"""Como se ve todo en Telegram.

Objetivo: que de un vistazo sepas que es lo siguiente y cuanto te queda,
sin tener que leer dos veces.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import escape as _escape_html


def escape(texto: str) -> str:
    """Escapa lo justo: < > &. Las comillas se dejan como estan."""
    return _escape_html(str(texto), quote=False)

from .config import cfg
from .models import Mail, Task
from .sources.studium import etiqueta
from .triage import fechas_mencionadas

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def semaforo(t: Task) -> str:
    h = t.horas_restantes
    if h is None:
        return "⚪"
    if h < 0:
        return "⚠️"
    if h < 24:
        return "🔴"
    if h < 72:
        return "🟠"
    if h < 168:
        return "🟡"
    return "🟢"


def cuanto_falta(t: Task, ahora: datetime | None = None) -> str:
    """'manana a las 23:59', 'en 4 dias', 'vencida hace 2 dias'."""
    venc = t.due_dt
    if venc is None:
        return "sin fecha"
    ahora = ahora or datetime.now(cfg.tz)
    delta = venc - ahora
    segundos = delta.total_seconds()
    hora = venc.strftime("%H:%M")

    if segundos < 0:
        pasados = -segundos
        if pasados < 3600:
            return f"venció hace {int(pasados // 60)} min"
        if pasados < 86400:
            return f"venció hace {int(pasados // 3600)} h"
        return f"venció hace {int(pasados // 86400)} días ({venc.day} {MESES[venc.month - 1]})"

    dias_naturales = (venc.date() - ahora.date()).days
    if segundos < 3600:
        return f"¡en {int(segundos // 60)} minutos! ({hora})"
    if dias_naturales == 0:
        return f"HOY a las {hora} (quedan {int(segundos // 3600)} h)"
    if dias_naturales == 1:
        return f"mañana a las {hora}"
    if dias_naturales < 7:
        return f"el {DIAS[venc.weekday()]} a las {hora} ({dias_naturales} días)"
    if dias_naturales < 31:
        return f"en {dias_naturales} días · {venc.day} {MESES[venc.month - 1]}, {hora}"
    return f"el {venc.day} de {MESES[venc.month - 1]} ({dias_naturales} días)"


def _asignatura_corta(nombre: str, tope: int = 38) -> str:
    nombre = nombre.split("(")[0].strip(" -–")
    return nombre if len(nombre) <= tope else nombre[: tope - 1] + "…"


def linea_tarea(t: Task, con_curso: bool = True) -> str:
    titulo = escape(t.title or "(sin titulo)")
    if t.url:
        titulo = f'<a href="{_escape_html(t.url, quote=True)}">{titulo}</a>'
    partes = [f"{semaforo(t)} <b>{titulo}</b>"]
    detalle = [etiqueta(t.kind)]
    if con_curso and t.course:
        detalle.append(escape(_asignatura_corta(t.course)))
    partes.append(f"   <i>{' · '.join(detalle)}</i>")
    partes.append(f"   🕒 {escape(cuanto_falta(t))}")
    return "\n".join(partes)


def lista_tareas(tareas: list[Task], titulo: str, vacio: str = "Nada pendiente. Disfrútalo.") -> str:
    if not tareas:
        return f"<b>{escape(titulo)}</b>\n\n✅ {escape(vacio)}"
    cuerpo = "\n\n".join(linea_tarea(t) for t in tareas)
    return f"<b>{escape(titulo)}</b>\n\n{cuerpo}"


def agenda_semana(tareas: list[Task]) -> str:
    """Igual que la lista pero partida por dias, que es como se piensa una semana."""
    if not tareas:
        return "<b>Esta semana</b>\n\n✅ No tienes nada con fecha esta semana."
    por_dia: dict[str, list[Task]] = {}
    for t in sorted(tareas, key=lambda x: x.due or 0):
        d = t.due_dt
        clave = f"{DIAS[d.weekday()].capitalize()} {d.day}" if d else "Sin fecha"
        por_dia.setdefault(clave, []).append(t)

    bloques = []
    for dia, items in por_dia.items():
        filas = "\n".join(
            f"  {semaforo(t)} <b>{escape(t.title)}</b> — {escape(_asignatura_corta(t.course, 28))}"
            f"  <i>{t.due_dt.strftime('%H:%M') if t.due_dt else ''}</i>"
            for t in items
        )
        bloques.append(f"<b>{escape(dia)}</b>\n{filas}")
    return "<b>Esta semana</b>\n\n" + "\n\n".join(bloques)


def otras_entregas(tareas: list[Task]) -> str:
    """Entregas del curso que Moodle no pone en la linea de tiempo.

    O no tienen fecha, o ya estan hechas, o ya paso su plazo. No sirven para
    avisar de nada, pero al mirar una asignatura conviene verlas.
    """
    if not tareas:
        return ""
    filas = []
    for t in tareas[:12]:
        cuando = cuanto_falta(t) if t.due else "sin fecha de entrega"
        titulo = escape(t.title)
        if t.url:
            titulo = f'<a href="{_escape_html(t.url, quote=True)}">{titulo}</a>'
        filas.append(f"  · {titulo} — <i>{escape(cuando)}</i>")
    return ("\n\n<b>Resto de entregas de la asignatura</b>\n"
            "<i>no están en tu lista de pendientes: o ya las hiciste, o no tienen fecha</i>\n"
            + "\n".join(filas))


def linea_correo(m: Mail, con_motivos: bool = False) -> str:
    asunto = escape(m.subject or "(sin asunto)")
    if m.url:
        asunto = f'<a href="{_escape_html(m.url, quote=True)}">{asunto}</a>'
    marca = "🔔" if m.clave else "•"
    sin_leer = " <b>·nuevo·</b>" if m.unread else ""
    clip = f" 📎 {len(m.adjuntos)}" if m.adjuntos else ""
    cuando = m.dt.strftime("%d %b %H:%M")
    filas = [f"{marca} <b>{asunto}</b>{sin_leer}{clip}",
             f"   <i>{escape(m.sender or m.sender_email)} · {cuando}</i>"]
    fechas = fechas_mencionadas(m)
    if fechas:
        filas.append(f"   📅 menciona: {escape(', '.join(fechas))}")
    # El resumen es lo que de verdad ahorra abrir el correo, asi que va antes
    # que el resto y sin cursiva: es lo primero que se lee.
    if m.resumen:
        filas.append(f"   ▸ {escape(m.resumen)}")
    elif m.snippet:
        filas.append(f"   <i>{escape(m.snippet[:140])}…</i>")
    if con_motivos and m.reasons:
        filas.append(f"   <i>por qué: {escape(', '.join(m.reasons))}</i>")
    return "\n".join(filas)


def lista_correos(correos: list[Mail], titulo: str, con_motivos: bool = False) -> str:
    if not correos:
        return f"<b>{escape(titulo)}</b>\n\n📭 Nada por aquí."
    return f"<b>{escape(titulo)}</b>\n\n" + "\n\n".join(
        linea_correo(m, con_motivos) for m in correos
    )


def resumen_diario(tareas: list[Task], correos: list[Mail]) -> str:
    ahora = datetime.now(cfg.tz)
    cabecera = f"☀️ <b>{DIAS[ahora.weekday()].capitalize()} {ahora.day} de {MESES[ahora.month - 1]}</b>"

    hoy = [t for t in tareas if t.due_dt and t.due_dt.date() == ahora.date()]
    pronto = [t for t in tareas if t.due_dt and 0 < (t.due_dt - ahora).days <= 7
              and t.due_dt.date() != ahora.date()]
    vencidas = [t for t in tareas if t.horas_restantes is not None and t.horas_restantes < 0]

    partes = [cabecera]
    if vencidas:
        partes.append("⚠️ <b>Se te ha pasado</b>\n" + "\n".join(
            f"  · {escape(t.title)} — {escape(_asignatura_corta(t.course, 28))} ({cuanto_falta(t)})"
            for t in vencidas[:5]))
    if hoy:
        partes.append("🔴 <b>Hoy</b>\n" + "\n".join(
            f"  · <b>{escape(t.title)}</b> — {escape(_asignatura_corta(t.course, 28))}"
            f" a las {t.due_dt.strftime('%H:%M')}" for t in hoy))
    else:
        partes.append("🔴 <b>Hoy</b>\n  Nada que entregar hoy.")
    if pronto:
        partes.append("📅 <b>Los próximos 7 días</b>\n" + "\n".join(
            f"  · {escape(t.title)} — {escape(cuanto_falta(t))}" for t in pronto[:6]))

    clave = [m for m in correos if m.clave]
    if clave:
        lineas = []
        for m in clave[:5]:
            lineas.append(f"  · <b>{escape(m.sender or m.sender_email)}</b>: "
                          f"{escape(m.subject[:64])}")
            if m.resumen:
                lineas.append(f"      ▸ {escape(m.resumen)}")
        partes.append("🔔 <b>Correos que conviene mirar</b>\n" + "\n".join(lineas))

    return "\n\n".join(partes)


def aviso_deadline(t: Task, bucket: str) -> str:
    titulares = {
        "7d": "📌 Queda menos de una semana",
        "3d": "🟠 Quedan menos de 3 días",
        "24h": "🔴 Queda menos de un día",
        "3h": "🚨 Últimas horas",
        "vencida": "⚠️ Se te ha pasado",
    }
    return (f"{titulares.get(bucket, 'Recordatorio')}\n\n{linea_tarea(t)}")


def fecha_movida(t: Task, antes: int | None) -> str:
    """Cuando el profesor cambia un plazo, eso hay que decirlo enseguida."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _Z

    if antes:
        previa = _dt.fromtimestamp(antes, tz=_Z("UTC")).astimezone(cfg.tz)
        antigua = f"{previa.day} {MESES[previa.month - 1]} a las {previa.strftime('%H:%M')}"
    else:
        antigua = "sin fecha"

    # Que te adelanten un plazo es peor noticia que que te lo amplien.
    adelantada = bool(antes and t.due and t.due < antes)
    titular = ("⚠️ <b>Te han ADELANTADO una entrega</b>" if adelantada
               else "📅 <b>Han cambiado una fecha</b>")
    return (f"{titular}\n\n{linea_tarea(t)}\n"
            f"   <i>antes: {escape(antigua)}</i>")


def _acortar_linea(linea: str, tope: int) -> str:
    """Una linea que no cabe se recorta SIN su HTML.

    Cortar a ciegas dejaria una etiqueta a medias (<b> sin cerrar) y Telegram
    rechaza el mensaje entero por HTML invalido; sin etiquetas no hay nada que
    romper.
    """
    if len(linea) <= tope:
        return linea
    limpia = re.sub(r"<[^>]*>", "", linea)
    return limpia[: tope - 1] + "…"


def trocear(texto: str, tope: int = 3900) -> list[str]:
    """Telegram corta a los 4096 caracteres; partimos por bloques, no a lo bruto."""
    if len(texto) <= tope:
        return [texto]

    piezas: list[str] = []
    for bloque in texto.split("\n\n"):
        if len(bloque) <= tope:
            piezas.append(bloque)
            continue
        # Un bloque solo mas largo que el tope: se parte por lineas, y la
        # linea que aun asi no quepa se recorta.
        actual = ""
        for linea in bloque.split("\n"):
            linea = _acortar_linea(linea, tope)
            if len(actual) + len(linea) + 1 > tope:
                if actual:
                    piezas.append(actual.rstrip())
                actual = ""
            actual += linea + "\n"
        if actual.strip():
            piezas.append(actual.rstrip())

    trozos, actual = [], ""
    for bloque in piezas:
        if len(actual) + len(bloque) + 2 > tope:
            trozos.append(actual.rstrip())
            actual = ""
        actual += bloque + "\n\n"
    if actual.strip():
        trozos.append(actual.rstrip())
    return trozos
