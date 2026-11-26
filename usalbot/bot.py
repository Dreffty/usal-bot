"""El bot de Telegram: comandos, preguntas sueltas y avisos por su cuenta."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta

from telegram import BotCommand, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import Conflict, InvalidToken, NetworkError, TimedOut
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from html import escape as escape_html

from . import alerts, db, format as fmt, nlu, pregunta as qa, sync
from .config import cfg
from .models import Task
from .sources import mail
from .sources.studium import Studium
from .texto import parecido, sin_tildes
from . import horario

log = logging.getLogger("usalbot.bot")

TECLADO = ReplyKeyboardMarkup(
    [[KeyboardButton("📋 Próximas"), KeyboardButton("🔴 Hoy")],
     [KeyboardButton("🗓 Semana"), KeyboardButton("🔔 Correos clave")]],
    resize_keyboard=True,
)

AYUDA = """<b>Qué te puedo contar</b>

Pregúntame en cristiano — <i>"¿qué clase tengo mañana a primera hora?"</i>, <i>"¿qué tengo esta semana?"</i>, <i>"¿cuándo se entrega la práctica de redes?"</i> — o tira de comandos:

📅 /horario [hoy|manana|semana] — tus clases de informática (Grupo B - pB4)
📝 /deberes &lt;texto&gt; — apuntar deberes (te aviso cada 2h desde las 16:00)
✅ /debereshechos — marcar deberes de hoy como hechos
📋 /tareas — todo lo pendiente en Studium
🔴 /hoy — lo que vence hoy
🟠 /manana — lo que vence mañana
🗓 /semana — agenda de los próximos 7 días
🚨 /urgente — lo que de verdad corre prisa
📚 /asignaturas — tus asignaturas de Studium
🔎 /asignatura &lt;nombre&gt; — pendiente de una sola
📬 /correos — los últimos correos
🔔 /clave — solo los correos que importan
🔍 /buscar &lt;texto&gt; — busca en tareas y correos
❓ /pregunta &lt;duda&gt; — te la busco en Studium, tu correo y la web de la USAL
📊 /resumen — el parte del día
✅ /hecho &lt;nombre&gt; — marcar una entrega como hecha
📦 /entregadas — las que Studium ya no lista (entregadas o quitadas)
⏳ /aplazar &lt;nombre&gt; &lt;tiempo&gt; — posponer avisos (p. ej. <code>/aplazar práctica 1d</code>)
↩️ /deshacer — deshacer el último /hecho
🔄 /sync — mirar Studium y el correo ahora mismo
⚙️ /estado — con qué cuentas estoy conectado

Además te aviso solo: 7 minutos antes de cada cambio de clase; una semana, tres días, un día y tres horas antes de cada entrega; si alguna se te pasa sin avisar, te lo digo una vez. Y cuando llega un correo que parece importante."""


# ------------------------------------------------------------------ utiles
def permitido(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id in cfg.allowed_ids)


async def responder(update: Update, texto: str, teclado=None) -> None:
    for trozo in fmt.trocear(texto):
        await update.effective_message.reply_text(
            trozo, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True, reply_markup=teclado,
        )


async def _asegurar_datos(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Si la base esta vacia (primer arranque), traer datos antes de responder."""
    if not db.get("ultimo_sync_studium"):
        await sync.sincronizar()


# --------------------------------------------------------------- comandos
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not permitido(update):
        await update.effective_message.reply_text(
            "Este bot es privado. Si es tuyo, añade tu id a TELEGRAM_ALLOWED_IDS en el .env.\n"
            f"Tu id es: {update.effective_user.id if update.effective_user else '?'}"
        )
        return
    await responder(
        update,
        "👋 <b>Listo.</b>\n\nMiro Studium y tu correo de la USAL por ti, y te digo qué toca "
        "sin tener que entrar asignatura por asignatura.\n\n" + AYUDA,
        TECLADO,
    )


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await responder(update, AYUDA, TECLADO)


async def cmd_tareas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _asegurar_datos(context)
    limite = 12
    if context.args and context.args[0].isdigit():
        limite = min(int(context.args[0]), 40)
    todas = db.tareas_pendientes(limite=60)
    # Lo vencido hace dias no se repite cada dia: ya lo cubre /hoy 48 h.
    visibles = [t for t in todas
                if t.horas_restantes is None or (t.horas_restantes or 0) > -48]
    tareas = visibles[:limite]
    texto = fmt.lista_tareas(tareas, "Lo que tienes pendiente")
    ocultas = len(todas) - len(visibles)
    if ocultas:
        texto += (f"\n\n<i>Hay {ocultas} vencida(s) de hace días que ya no muestro. "
                  f"Si te hacen falta, pídemelas y las busco.</i>")
    await responder(update, texto)


async def cmd_hoy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _asegurar_datos(context)
    ahora = datetime.now(cfg.tz)
    fin = ahora.replace(hour=23, minute=59, second=59)
    tareas = db.tareas_entre(int(ahora.timestamp()) - 86400 * 7, int(fin.timestamp()))
    hoy = [t for t in tareas if t.due_dt and t.due_dt.date() == ahora.date()]
    vencidas = [t for t in tareas if t.horas_restantes is not None and t.horas_restantes < 0]

    texto = fmt.lista_tareas(hoy, "Para hoy en Studium", "Hoy no vence nada en Studium.")
    # Solo lo de las ultimas 48 h: sobre eso aun se puede hacer algo (pedir
    # prorroga, entregar tarde). Lo de hace una semana, repetido cada dia,
    # deja de leerse.
    recientes = [t for t in vencidas if (t.horas_restantes or 0) > -48]
    if recientes:
        texto += "\n\n" + fmt.lista_tareas(recientes, "⚠️ Y esto se te pasó hace poco")
    if len(vencidas) > len(recientes):
        resto = len(vencidas) - len(recientes)
        texto += (f"\n\n<i>Hay {resto} cosa(s) más vencidas de hace tiempo. "
                  f"Las tienes en /tareas.</i>")

    sesiones_h = horario.sesiones_dia(ahora.weekday())
    if sesiones_h:
        texto += f"\n\n🎓 <i>Hoy tienes {len(sesiones_h)} clases. Pregúntame 'clases' o usa /horario para verlas.</i>"
    await responder(update, texto)


async def cmd_manana(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _asegurar_datos(context)
    ahora = datetime.now(cfg.tz)
    base = ahora.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    tareas = db.tareas_entre(int(base.timestamp()),
                             int((base + timedelta(days=1)).timestamp()) - 1)
    texto = fmt.lista_tareas(tareas, "Para mañana en Studium", "Mañana no vence nada en Studium.")
    sesiones_m = horario.sesiones_dia((ahora.weekday() + 1) % 7)
    if sesiones_m:
        primera = sesiones_m[0]
        texto += f"\n\n🎓 <i>En clases mañana empiezas a las {primera.inicio.strftime('%H:%M')} con {primera.asignatura} ({primera.aula}). Usa /horario o pregúntame para verlas.</i>"
    await responder(update, texto)


async def cmd_semana(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _asegurar_datos(context)
    ahora = datetime.now(cfg.tz)
    tareas = db.tareas_entre(int(ahora.timestamp()), int((ahora + timedelta(days=7)).timestamp()))
    await responder(update, fmt.agenda_semana(tareas))


async def cmd_horario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = " ".join(context.args or []).strip()
    ahora = datetime.now(cfg.tz)
    pregunta = f"horario {args}" if args else "horario"
    respuesta = horario.responder_consulta(pregunta, ahora)
    await responder(update, respuesta)


async def cmd_urgente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _asegurar_datos(context)
    tareas = [t for t in db.tareas_pendientes(limite=40)
              if t.horas_restantes is not None and t.horas_restantes < 72]
    if not tareas:
        siguiente = db.tareas_pendientes(limite=1)
        texto = ("✅ <b>Nada que corra prisa</b>\n\nLo siguiente:\n\n" + fmt.linea_tarea(siguiente[0])
                 if siguiente else "✅ <b>Nada pendiente.</b>")
        await responder(update, texto)
        return
    await responder(update, fmt.lista_tareas(tareas[:8], "🚨 Esto es lo que corre prisa"))


async def cmd_asignaturas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _asegurar_datos(context)
    cursos = db.cursos()
    if not cursos:
        await responder(update, "No tengo tus asignaturas todavía. Prueba /sync.")
        return
    pendientes = db.tareas_pendientes(limite=100)
    cuenta: dict[int, int] = {}
    for t in pendientes:
        cuenta[t.course_id] = cuenta.get(t.course_id, 0) + 1
    filas = []
    for c in cursos:
        n = cuenta.get(c.id, 0)
        marca = f"<b>{n}</b> pendiente{'s' if n != 1 else ''}" if n else "al día"
        filas.append(f'• <a href="{c.url}">{c.name}</a> — {marca}')
    await responder(update, "<b>Tus asignaturas</b>\n\n" + "\n".join(filas))


async def cmd_asignatura(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texto = " ".join(context.args or [])
    if not texto:
        await responder(update, "Dime cuál: <code>/asignatura redes</code>")
        return
    await _mostrar_asignatura(update, texto)


async def _mostrar_asignatura(update: Update, texto: str) -> None:
    # Primero se intenta casar con el nombre real de una asignatura tuya; asi
    # "redes" encuentra "Redes de Computadores (Grado en...)" y "calculo"
    # encuentra "Cálculo I" aunque lo escribas sin tilde.
    curso = db.resolver_curso(texto)
    if curso:
        pendientes = db.tareas_del_curso_id(curso.id)
        titulo = curso.name.split("(")[0].strip()
    else:
        pendientes = db.tareas_de_curso(texto)
        titulo = texto
        if not pendientes:
            await responder(update, f"No encuentro nada con «{texto}». Mira /asignaturas.")
            return

    cuerpo = (fmt.lista_tareas(pendientes, f"Pendiente en {titulo}",
                               "Nada pendiente ahí.") if pendientes
              else f"<b>{titulo}</b>\n\n✅ Nada pendiente ahí.")

    # Las entregas que Moodle no pone en la linea de tiempo (sin fecha, o ya
    # hechas) no salen en lo de arriba, y al mirar una asignatura interesan.
    if curso and not cfg.usa_calendario:
        try:
            todas = await Studium().entregas_del_curso(curso.id)
            conocidas = {sin_tildes(t.title) for t in pendientes}
            sueltas = [t for t in todas if sin_tildes(t.title) not in conocidas]
            cuerpo += fmt.otras_entregas(sueltas)
        except Exception:
            pass  # si Studium no contesta, al menos se ve lo que ya sabemos

    await responder(update, cuerpo)


async def cmd_correos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _asegurar_datos(context)
    await responder(update, fmt.lista_correos(db.correos(limite=10), "Últimos correos"))


async def cmd_clave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _asegurar_datos(context)
    correos = db.correos(limite=10, solo_clave=True)
    await responder(update, fmt.lista_correos(correos, "🔔 Correos que importan", con_motivos=True))


async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texto = " ".join(context.args or []).strip()
    if not texto:
        await responder(update, "Dime qué busco: <code>/buscar cálculo</code>")
        return
    if not await _buscar(update, texto):
        await responder(update, f"No hay nada con «{texto}» ni en tareas ni en correos.")


async def _buscar(update: Update, texto: str) -> bool:
    """Busca en Studium y correo. Devuelve si encontró algo: quien llama
    decide si tirar de pregunta abierta cuando no hay nada."""
    tareas = db.tareas_de_curso(texto, limite=8)
    correos = db.buscar_correos(texto, limite=5)
    partes = []
    if tareas:
        partes.append(fmt.lista_tareas(tareas, f"Tareas con «{texto}»"))
    if correos:
        partes.append(fmt.lista_correos(correos, f"Correos con «{texto}»"))
    if not partes:
        return False
    await responder(update, "\n\n".join(partes))
    return True


async def cmd_pregunta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texto = " ".join(context.args or []).strip()
    if not texto:
        await responder(update, "Dime la duda entera: "
                                "<code>/pregunta cómo pido el cambio de curso</code>")
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    await _asegurar_datos(context)
    await responder(update, await qa.responder_pregunta(texto))


async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _asegurar_datos(context)
    desde = int((datetime.now(cfg.tz) - timedelta(days=2)).timestamp())
    await responder(update, fmt.resumen_diario(db.tareas_pendientes(limite=40),
                                               db.correos(limite=20, desde=desde)))


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_chat.send_action(ChatAction.TYPING)
    res = await sync.sincronizar()
    lineas = [f"🔄 <b>Actualizado</b>",
              f"• {res.total_pendientes} tareas pendientes en Studium",
              f"• {len(res.tareas_nuevas)} nuevas desde la última vez",
              f"• {len(res.correos_nuevos)} correos nuevos"]
    if res.entregadas:
        lineas.append(f"• {res.entregadas} han dejado de estar pendientes (entregadas o cerradas)")
    for e in res.errores:
        lineas.append(f"⚠️ {e}")
    await responder(update, "\n".join(lineas))
    if res.tareas_nuevas:
        await responder(update, fmt.lista_tareas(res.tareas_nuevas[:6], "Nuevo desde la última vez"))


async def cmd_hecho(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Marcar una entrega como hecha.

    Con la clave de API esto sobra: Moodle quita de la lista lo que ya has
    entregado. Leyendo el calendario no hay forma de saberlo, asi que se dice
    a mano.
    """
    texto = " ".join(context.args or [])
    if not texto:
        await responder(update, "Dime cuál: <code>/hecho práctica 2</code>")
        return

    candidatas = db.tareas_pendientes(limite=60)
    if not candidatas:
        await responder(update, "No tienes nada pendiente que marcar.")
        return

    puntuadas = [(max(parecido(texto, t.title), parecido(texto, f"{t.title} {t.course}")), t)
                 for t in candidatas]
    nota = max(n for n, _ in puntuadas)
    if nota < 0.5:
        await responder(update, f"No sé cuál es «{escape_html(texto)}». Mira /tareas.")
        return

    # Con "practica" pueden empatar tres tareas distintas. Elegir una a ciegas
    # te deja sin avisos de algo que no has entregado, asi que mejor preguntar.
    empatadas = [t for n, t in puntuadas if n >= nota - 0.001]
    if len(empatadas) > 1:
        filas = "\n".join(f"  · <code>/hecho {escape_html(t.title[:38])}</code>"
                           for t in empatadas[:5])
        await responder(update,
                        f"Hay {len(empatadas)} que encajan con «{escape_html(texto)}». "
                        f"¿Cuál?\n\n{filas}")
        return

    mejor = empatadas[0]
    db.marcar_hecha(mejor.id)
    db.put("ultima_hecha", mejor.id)
    await responder(update, f"✅ Marcada como hecha: <b>{escape_html(mejor.title)}</b>"
                            f"\n<i>{escape_html(mejor.course)}</i>"
                            f"\n\nYa no te avisaré de ella. Si me he equivocado: /deshacer")


async def cmd_deshacer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Revierte el ultimo /hecho: marcar la que no era te deja sin avisos."""
    ultima = db.get("ultima_hecha")
    if not ultima:
        await responder(update, "No hay nada reciente que deshacer.")
        return
    tarea = db.tarea_por_id(ultima)
    if tarea is None or not db.desmarcar_hecha(ultima):
        await responder(update, "Eso ya no se puede deshacer.")
        return
    db.put("ultima_hecha", "")
    await responder(update, f"↩️ <b>{escape_html(tarea.title)}</b> vuelve a estar "
                            f"pendiente.\n\nTe volveré a avisar de ella.")


async def cmd_entregadas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lo que Studium dejo de listar: entregado o quitado por el profe.

    No se borra, se oculta. Si pasas un nombre (``/entregadas practica``)
    intenta devolverla a pendientes, por si Studium la quito por error.
    """
    await _asegurar_datos(context)
    texto = " ".join(context.args or []).strip()
    if texto:
        ocultas = db.tareas_ocultas(limite=60)
        if ocultas:
            puntuadas = [(max(parecido(texto, t.title), parecido(texto, f"{t.title} {t.course}")), t)
                         for t in ocultas]
            nota, mejor = max(puntuadas, key=lambda x: x[0])
            if nota >= 0.5 and db.recuperar_oculta(mejor.id):
                await responder(update, f"↩️ <b>{escape_html(mejor.title)}</b> vuelve a estar "
                                        f"pendiente.\n\nTe volveré a avisar de ella.")
                return
        await responder(update, f"No veo ninguna oculta que encaje con «{escape_html(texto)}».")
        return
    ocultas = db.tareas_ocultas(limite=20)
    await responder(update, fmt.lista_tareas(
        ocultas, "📦 Entregadas o quitadas de Studium",
        "Nada por aquí. Cuando una tarea deja de salir en Studium sin que "
        "hagas /hecho, aparece aquí en vez de borrarse."))


_RE_PLAZO = re.compile(r"\s*(\d+)\s*(m|minutos?|h|horas?|d|d[ií]as?)\s*$", re.IGNORECASE)


async def _aplazar_por_texto(update: Update, texto: str) -> None:
    texto = (texto or "").strip()
    if not texto:
        await responder(update, "Dime cuál pospongo: <code>/aplazar práctica 2 1d</code>\n"
                                "Tiempos: <code>30m</code>, <code>3h</code>, <code>1d</code>, <code>7d</code>.")
        return
    segundos = 24 * 3600
    m = _RE_PLAZO.search(texto)
    if m:
        cantidad = int(m.group(1))
        unidad = m.group(2).lower()
        segundos = cantidad * (60 if unidad.startswith("m") else 3600 if unidad.startswith("h") else 86400)
        texto = texto[:m.start()].strip()
    if not texto:
        await responder(update, "Dime cuál pospongo: <code>/aplazar práctica 2 1d</code>")
        return
    candidatas = db.tareas_pendientes(limite=60)
    if not candidatas:
        await responder(update, "No tienes nada pendiente que aplazar.")
        return
    puntuadas = [(max(parecido(texto, t.title), parecido(texto, f"{t.title} {t.course}")), t)
                 for t in candidatas]
    nota = max(n for n, _ in puntuadas)
    if nota < 0.5:
        await responder(update, f"No sé cuál es «{escape_html(texto)}». Mira /tareas.")
        return
    empatadas = [t for n, t in puntuadas if n >= nota - 0.001]
    if len(empatadas) > 1:
        filas = "\n".join(f"  · <code>/aplazar {escape_html(t.title[:38])}</code>"
                          for t in empatadas[:5])
        await responder(update, f"Hay {len(empatadas)} que encajan con «{escape_html(texto)}». "
                                f"¿Cuál?\n\n{filas}")
        return
    mejor = empatadas[0]
    hasta = int(time.time()) + segundos
    db.aplazar(mejor.id, hasta)
    if segundos % 86400 == 0:
        etiqueta = f"{segundos // 86400}d"
    elif segundos % 3600 == 0:
        etiqueta = f"{segundos // 3600}h"
    else:
        etiqueta = f"{segundos // 60}m"
    cuando = datetime.fromtimestamp(hasta, cfg.tz).strftime("%d/%m %H:%M")
    await responder(update, f"⏳ <b>{escape_html(mejor.title)}</b> aplazada {etiqueta} "
                            f"(hasta el {cuando}).\n\n<i>No te avisaré de ella hasta entonces.</i>")


async def cmd_aplazar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pospone los avisos de una tarea: /aplazar <nombre> <30m|3h|1d>."""
    await _asegurar_datos(context)
    await _aplazar_por_texto(update, " ".join(context.args or []))


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lineas = ["<b>Estado</b>"]
    if cfg.usa_calendario:
        try:
            from .sources import studium_ical
            n, c = await studium_ical.comprobar(cfg.studium_ical_url)
            lineas.append(f"✅ Studium (calendario exportado): {n} eventos, {c} asignaturas")
            lineas.append("   <i>ojo: el calendario no sabe lo que ya entregaste;"
                          " usa /hecho para quitarlo</i>")
        except Exception as e:
            lineas.append(f"❌ Calendario de Studium: {e}")
    else:
        try:
            lineas.append(f"✅ Studium (API): {await Studium().comprobar()}")
        except Exception as e:
            lineas.append(f"❌ Studium: {e}")
    try:
        lineas.append(f"✅ Correo ({cfg.mail_backend}): {await mail.comprobar()}")
    except Exception as e:
        lineas.append(f"❌ Correo: {e}")

    for clave, etiqueta in (("ultimo_sync_studium", "Studium"), ("ultimo_sync_correo", "Correo")):
        marca = db.get(clave)
        if marca:
            hace = int((time.time() - int(marca)) // 60)
            lineas.append(f"🕒 {etiqueta} mirado hace {hace} min")
    lineas.append(f"🧠 Lenguaje natural: {'modelo + reglas' if cfg.llm_enabled else 'solo reglas'}")
    await responder(update, "\n".join(lineas))


# ------------------------------------------------------- preguntas sueltas
ATAJOS = {
    "📋 próximas": cmd_tareas, "🔴 hoy": cmd_hoy,
    "🗓 semana": cmd_semana, "🔔 correos clave": cmd_clave,
    "📅 horario": cmd_horario, "🎓 clases": cmd_horario,
}



async def cmd_deberes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texto = update.effective_message.text or ""
    await procesar_mensaje_deberes(update, context, texto)

async def texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pregunta = (update.effective_message.text or "").strip()

    if await procesar_mensaje_deberes(update, context, pregunta):
        return

    atajo = ATAJOS.get(pregunta.lower())
    if atajo:
        await atajo(update, context)
        return

    await update.effective_chat.send_action(ChatAction.TYPING)
    intencion = await nlu.entender(pregunta)
    await _asegurar_datos(context)

    if intencion.nombre == "ayuda":
        await cmd_ayuda(update, context)
    elif intencion.nombre == "horario":
        ahora = datetime.now(cfg.tz)
        respuesta = horario.responder_consulta(pregunta, ahora)
        await responder(update, respuesta)
    elif intencion.nombre == "hoy":
        await cmd_hoy(update, context)
    elif intencion.nombre == "manana":
        await cmd_manana(update, context)
    elif intencion.nombre == "semana":
        await cmd_semana(update, context)
    elif intencion.nombre == "urgente":
        await cmd_urgente(update, context)
    elif intencion.nombre == "asignaturas":
        await cmd_asignaturas(update, context)
    elif intencion.nombre == "resumen":
        await cmd_resumen(update, context)
    elif intencion.nombre == "correos_clave":
        await cmd_clave(update, context)
    elif intencion.nombre == "correos":
        await cmd_correos(update, context)
    elif intencion.nombre == "entregadas":
        await cmd_entregadas(update, context)
    elif intencion.nombre == "aplazar" and intencion.filtro:
        await _asegurar_datos(context)
        await _aplazar_por_texto(update, intencion.filtro)
    elif intencion.nombre == "aplazar":
        await cmd_aplazar(update, context)
    elif intencion.nombre == "asignatura" and intencion.filtro:
        await _mostrar_asignatura(update, intencion.filtro)
    elif intencion.nombre in {"buscar", "cuando", "pregunta"}:
        # Todo texto libre es pregunta abierta: `responder_pregunta` ya mira
        # Studium + correo + web. Antes "buscar" solo miraba lo local y
        # contestaba "no hay nada", que es lo de tu captura.
        await responder(update, await qa.responder_pregunta(pregunta))
    else:
        await cmd_tareas(update, context)


# ------------------------------------------------------------------- jobs
async def _difundir(context: ContextTypes.DEFAULT_TYPE, texto: str) -> None:
    for chat_id in cfg.allowed_ids:
        for trozo in fmt.trocear(texto):
            try:
                await context.bot.send_message(chat_id, trozo, parse_mode=ParseMode.HTML,
                                               disable_web_page_preview=True)
            except Exception:
                log.exception("no he podido escribir a %s", chat_id)




async def job_recordatorio_horario(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Revisa cada minuto si hay clase dentro de 7 minutos y avisa."""
    sesion = horario.recordatorio_7min()
    if sesion:
        hoy = datetime.now(cfg.tz).strftime("%Y-%m-%d")
        clave = f"horario_avisado:{hoy}:{sesion.inicio.strftime('%H:%M')}"
        if db.get(clave) == "1":
            return
        db.put(clave, "1")
        sub = f" [{sesion.subgrupo}]" if sesion.subgrupo else ""
        nota = f" ({sesion.notas})" if sesion.notas else ""
        texto = f"⏰ <b>Recordatorio de clase en 7 min!</b>\n\n📚 {sesion.asignatura}{sub}{nota}\n🏫 {sesion.aula}\n🕑 {sesion.inicio.strftime('%H:%M')} - {sesion.fin.strftime('%H:%M')}"
        await _difundir(context, texto)

async def job_recordatorio_deberes(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Revisa si hay deberes y avisa a partir de las 16:00 cada 2h."""
    ahora = datetime.now(cfg.tz)
    # Solo de 16:00 a 23:59
    if ahora.hour < 16:
        return
        
    dia_str = ahora.strftime("%Y-%m-%d")
    deberes = db.get(f"deberes:{dia_str}")
    hecho = db.get(f"deberes_hechos:{dia_str}")
    
    if deberes and hecho != "1":
        ultima_vez = db.get(f"deberes_avisado:{dia_str}")
        if ultima_vez:
            ultima_hora = datetime.fromtimestamp(float(ultima_vez), cfg.tz)
            if (ahora - ultima_hora).total_seconds() < 7000: # ~2 horas
                return
                
        db.put(f"deberes_avisado:{dia_str}", str(ahora.timestamp()))
        await _difundir(context, f"📚 <b>Recordatorio de Deberes</b>\n\nNo olvides hacer los deberes de hoy:\n{deberes}\n\n<i>Dime 'deberes hechos' cuando termines.</i>")

async def procesar_mensaje_deberes(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str) -> bool:
    """Intercepta mensajes sobre deberes."""
    texto_lower = (texto or "").strip().lower()
    dia_str = datetime.now(cfg.tz).strftime("%Y-%m-%d")

    # Marcar hechos
    if (
        "deberes hechos" in texto_lower
        or "ya estan hechos" in texto_lower
        or "ya están hechos" in texto_lower
        or texto_lower in {"/debereshechos", "debereshechos"}
    ):
        db.put(f"deberes_hechos:{dia_str}", "1")
        await responder(update, "✅ Deberes marcados como hechos. ¡Buen trabajo!")
        return True

    # Consultar deberes si solo pone /deberes o "¿qué deberes tengo?"
    if texto_lower in {"/deberes", "deberes", "que deberes tengo", "cuales son los deberes", "deberes de hoy"}:
        deberes = db.get(f"deberes:{dia_str}")
        hecho = db.get(f"deberes_hechos:{dia_str}") == "1"
        if not deberes:
            await responder(update, "📝 No tienes deberes apuntados para hoy.\n\nPara apuntar: <code>/deberes Terminar la práctica de programación</code>")
        elif hecho:
            await responder(update, f"✅ Los deberes de hoy ya están completados:\n\n{escape_html(deberes)}")
        else:
            await responder(update, f"📝 <b>Deberes de hoy pendientes:</b>\n\n{escape_html(deberes)}\n\n<i>Te recordaré cada 2h desde las 16:00. Para quitarlos dime 'deberes hechos'.</i>")
        return True

    # Apuntar deberes
    if (
        "deberes:" in texto_lower
        or ("deberes " in texto_lower and ("mandado" in texto_lower or "apunta" in texto_lower or "tengo" in texto_lower or "para hoy" in texto_lower))
        or texto_lower.startswith("/deberes ")
    ):
        # Extraer lo que viene después
        idx = texto_lower.find("deberes")
        deberes = texto[idx:]
        if deberes.startswith("deberes "):
            deberes = deberes[8:].strip()
        elif deberes.startswith("deberes:"):
            deberes = deberes[8:].strip()

        if not deberes:
            await responder(update, "Dime qué deberes te han mandado: <code>/deberes Terminar ejercicio 3</code>")
            return True

        db.put(f"deberes:{dia_str}", deberes)
        db.put(f"deberes_hechos:{dia_str}", "0")
        db.put(f"deberes_avisado:{dia_str}", "")
        await responder(update, f"📝 <b>Apuntado para hoy:</b>\n\n{escape_html(deberes)}\n\n<i>Te avisaré a partir de las 16:00 cada dos horas. Para desactivarlo, dime 'deberes hechos'.</i>")
        return True

    return False

async def job_sync(context: ContextTypes.DEFAULT_TYPE) -> None:
    primera_vez = not db.get("arranque_hecho")
    res = await sync.sincronizar()

    if primera_vez:
        # La primera vez todo es "nuevo". En vez de soltar veinte mensajes de
        # golpe, se toma nota de lo que ya hay y se manda un solo resumen.
        db.marcar_correos_notificados(
            [m.id for m in db.correos(limite=300, solo_clave=True)])
        for tarea, tramo in alerts.avisos_a_enviar(db.tareas_pendientes(limite=60)):
            alerts.confirmar(tarea, tramo)
        db.put("arranque_hecho", "1")
        res.fechas_movidas.clear()
        await _difundir(context, "👋 <b>Ya estoy mirando por ti.</b> Esto es lo que "
                                 "hay ahora mismo; a partir de aquí solo te aviso "
                                 "de lo que cambie.\n\n"
                        + fmt.resumen_diario(db.tareas_pendientes(limite=40),
                                             db.correos(limite=20)))
        return

    if res.tareas_nuevas:
        await _difundir(context, fmt.lista_tareas(res.tareas_nuevas[:5],
                                                  "🆕 Tarea nueva en Studium"))

    # Que muevan un plazo es lo que peor sienta descubrir tarde.
    for tarea, antes in res.fechas_movidas[:4]:
        await _difundir(context, fmt.fecha_movida(tarea, antes))

    pendientes = db.tareas_pendientes(limite=60)
    for tarea, tramo in alerts.avisos_a_enviar(pendientes):
        await _difundir(context, fmt.aviso_deadline(tarea, tramo))
        alerts.confirmar(tarea, tramo)

    desde = int(time.time()) - 3 * 86400
    clave = db.correos_clave_sin_notificar(desde)
    if clave:
        await _difundir(context, fmt.lista_correos(clave[:5], "🔔 Correo que parece importante",
                                                   con_motivos=True))
        db.marcar_correos_notificados([m.id for m in clave])

    if res.hubo_error and db.get("ultimo_error") != res.errores[0]:
        db.put("ultimo_error", res.errores[0])
        await _difundir(context, f"⚠️ {res.errores[0]}")
    elif not res.hubo_error:
        db.put("ultimo_error", "")

    # La base crece sola: tareas hechas, correos y avisos viejos se purgan
    # una vez al día (por defecto, lo de hace más de 90 días).
    try:
        ultima = int(db.get("ultima_purga") or 0)
    except ValueError:
        ultima = 0
    if time.time() - ultima > 86400:
        try:
            n_tareas, n_correos = db.purgar_antiguos()
            db.put("ultima_purga", str(int(time.time())))
            if n_tareas or n_correos:
                log.info("purga: %d tareas y %d correos viejos fuera", n_tareas, n_correos)
        except Exception:
            log.exception("la purga automática ha fallado")


async def job_resumen(context: ContextTypes.DEFAULT_TYPE) -> None:
    desde = int((datetime.now(cfg.tz) - timedelta(days=1)).timestamp())
    await _difundir(context, fmt.resumen_diario(db.tareas_pendientes(limite=40),
                                                db.correos(limite=20, desde=desde)))


# ------------------------------------------------------------------ errores
async def al_fallar(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Un fallo no debe dejar media traza en el log y nada mas.

    El caso que mas despista es el de dos copias del bot a la vez: Telegram
    corta a una y, sin esto, lo unico que se ve es una excepcion larguisima
    que no dice que hacer.
    """
    error = context.error

    if isinstance(error, Conflict):
        log.error(
            "Hay OTRA copia de este bot hablando con Telegram y se están "
            "pisando. Deja solo una: `launchctl list | grep usal` para ver si "
            "está el servicio, y `ps aux | grep run.py` para las sueltas."
        )
        return

    if isinstance(error, (NetworkError, TimedOut)):
        log.warning("Telegram no responde ahora mismo (%s). Se reintenta solo.",
                    type(error).__name__)
        return

    if isinstance(error, InvalidToken):
        log.error("El TELEGRAM_TOKEN del .env no vale. Pídele uno nuevo a @BotFather.")
        return

    log.exception("Fallo no previsto atendiendo una actualización", exc_info=error)


# ------------------------------------------------------------------ arranque
async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("horario", "Tus clases (hoy, mañana, semana)"),
        BotCommand("deberes", "Apuntar o ver deberes de hoy"),
        BotCommand("debereshechos", "Marcar deberes como hechos"),
        BotCommand("tareas", "Todo lo pendiente"),
        BotCommand("hoy", "Lo que vence hoy"),
        BotCommand("manana", "Lo que vence mañana"),
        BotCommand("semana", "Agenda de 7 días"),
        BotCommand("urgente", "Lo que corre prisa"),
        BotCommand("asignaturas", "Tus asignaturas"),
        BotCommand("correos", "Últimos correos"),
        BotCommand("clave", "Correos importantes"),
        BotCommand("buscar", "Buscar en tareas y correos"),
        BotCommand("pregunta", "Duda abierta: Studium + correo + USAL"),
        BotCommand("resumen", "El parte del día"),
        BotCommand("hecho", "Marcar una entrega como hecha"),
        BotCommand("entregadas", "Entregadas o quitadas de Studium"),
        BotCommand("aplazar", "Posponer avisos de una tarea"),
        BotCommand("deshacer", "Deshacer el último /hecho"),
        BotCommand("sync", "Mirar Studium ahora"),
        BotCommand("estado", "Ver conexiones"),
        BotCommand("ayuda", "Qué sé hacer"),
    ])


def construir() -> Application:
    db.init()
    app = Application.builder().token(cfg.telegram_token).post_init(_post_init).build()

    solo_mios = filters.User(user_id=list(cfg.allowed_ids)) if cfg.allowed_ids else filters.ALL

    app.add_handler(CommandHandler("start", cmd_start))
    for nombre, fn in [
        ("ayuda", cmd_ayuda), ("help", cmd_ayuda), ("horario", cmd_horario), ("clases", cmd_horario),
        ("tareas", cmd_tareas), ("hoy", cmd_hoy), ("manana", cmd_manana),
        ("semana", cmd_semana), ("urgente", cmd_urgente), ("asignaturas", cmd_asignaturas),
        ("asignatura", cmd_asignatura), ("correos", cmd_correos), ("clave", cmd_clave),
        ("buscar", cmd_buscar), ("pregunta", cmd_pregunta), ("resumen", cmd_resumen), ("sync", cmd_sync),
        ("estado", cmd_estado), ("hecho", cmd_hecho), ("deshacer", cmd_deshacer),
        ("entregadas", cmd_entregadas), ("aplazar", cmd_aplazar),
    ]:
        app.add_handler(CommandHandler(nombre, fn, filters=solo_mios))

    app.add_handler(CommandHandler("deberes", cmd_deberes, filters=solo_mios))
    app.add_handler(CommandHandler("debereshechos", cmd_deberes, filters=solo_mios))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & solo_mios, texto_libre))
    app.add_error_handler(al_fallar)

    cola = app.job_queue
    cola.run_repeating(job_sync, interval=cfg.sync_minutes * 60, first=20, name="sync")
    cola.run_repeating(job_recordatorio_horario, interval=60, first=10, name="horario_7min")
    cola.run_repeating(job_recordatorio_deberes, interval=300, first=30, name="deberes_2h")
    if cfg.daily_digest_at:
        cola.run_daily(job_resumen, time=cfg.daily_digest_at.replace(tzinfo=cfg.tz),
                       name="resumen")
    return app
