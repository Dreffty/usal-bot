"""
Horario Grupo B - Grado en Ingeniería Informática (1er curso 2026-2027, 1º Semestre)
Subgrupo de prácticas: pB4
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
import re
from typing import Optional

from .texto import sin_tildes


class TipoClase(Enum):
    TEORIA = "Teoría"
    PRACTICAS = "Prácticas"
    SEMINARIO = "Seminario"


@dataclass(frozen=True)
class Sesion:
    dia: int  # 0=Lunes, ..., 4=Viernes
    inicio: time
    fin: time
    asignatura: str
    tipo: TipoClase
    aula: str
    subgrupo: Optional[str] = None
    notas: str = ""


# Sesiones de Teoría para todo el Grupo B (Apellidos LB-Z)
TEORIA_B = [
    # Lunes
    Sesion(0, time(9, 0), time(10, 0), "Fundamentos Físicos", TipoClase.TEORIA, "Aula Magna II", notas="5 sesiones en el cuatrimestre"),
    Sesion(0, time(12, 0), time(13, 0), "Programación I", TipoClase.TEORIA, "Aula Magna II"),
    Sesion(0, time(13, 0), time(14, 30), "Álgebra Lineal y Geometría", TipoClase.TEORIA, "Aula Magna II"),

    # Martes
    Sesion(1, time(11, 0), time(12, 0), "Fundamentos Físicos", TipoClase.TEORIA, "Aula Magna II"),
    Sesion(1, time(12, 0), time(13, 0), "Computadores I", TipoClase.TEORIA, "Aula Magna II"),
    Sesion(1, time(13, 0), time(14, 0), "Estadística", TipoClase.TEORIA, "Aula Magna II"),

    # Miércoles
    Sesion(2, time(9, 0), time(10, 0), "Fundamentos Físicos", TipoClase.TEORIA, "Aula Magna II", notas="5 sesiones en el cuatrimestre"),
    Sesion(2, time(12, 0), time(13, 0), "Programación I", TipoClase.TEORIA, "Aula Magna II"),
    Sesion(2, time(13, 0), time(14, 30), "Álgebra Lineal y Geometría", TipoClase.TEORIA, "Aula Magna II"),

    # Jueves
    Sesion(3, time(11, 0), time(12, 0), "Fundamentos Físicos", TipoClase.TEORIA, "Aula Magna II"),
    Sesion(3, time(12, 0), time(13, 0), "Computadores I", TipoClase.TEORIA, "Aula Magna II"),
    Sesion(3, time(13, 0), time(14, 0), "Estadística", TipoClase.TEORIA, "Aula Magna II"),

    # Viernes
    Sesion(4, time(12, 0), time(14, 0), "Estadística (*)", TipoClase.SEMINARIO, "Aula Magna II", notas="Seminario primeras semanas"),
]

# Prácticas específicas que le corresponden al subgrupo pB4
PRACTICAS_PB4 = [
    # Lunes
    Sesion(0, time(10, 0), time(12, 0), "Programación I", TipoClase.PRACTICAS, "Aula Inform. 3", subgrupo="PB4"),

    # Martes
    Sesion(1, time(9, 0), time(11, 0), "Fundamentos Físicos", TipoClase.PRACTICAS, "Aula VI (Tr.) o Lab. Electrónica", subgrupo="PB4"),

    # Miércoles
    Sesion(2, time(10, 0), time(12, 0), "Estadística (*)", TipoClase.PRACTICAS, "Aula Inform. 4", subgrupo="PB4/1", notas="Mitad grupo PB4"),

    # Jueves
    Sesion(3, time(17, 0), time(18, 0), "Álgebra Lineal y Geometría", TipoClase.PRACTICAS, "Aula D2", subgrupo="PB4"),
    Sesion(3, time(18, 0), time(20, 0), "Computadores I", TipoClase.PRACTICAS, "Aula Inform. 2", subgrupo="PB4"),

    # Viernes
    Sesion(4, time(10, 0), time(12, 0), "Estadística (*)", TipoClase.PRACTICAS, "Aula Inform. 7", subgrupo="PB4/2", notas="Mitad grupo PB4"),
]

HORARIO_COMPLETO = sorted(TEORIA_B + PRACTICAS_PB4, key=lambda s: (s.dia, s.inicio))

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DIAS_BUSQUEDA = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6,
}


def sesiones_dia(dia: int) -> list[Sesion]:
    return [s for s in HORARIO_COMPLETO if s.dia == dia]


def obtener_sesiones_hoy() -> list[Sesion]:
    return sesiones_dia(datetime.now().weekday())


def primera_sesion_dia(dia: int) -> Optional[Sesion]:
    sesiones = sesiones_dia(dia)
    return sesiones[0] if sesiones else None


def ultima_sesion_dia(dia: int) -> Optional[Sesion]:
    sesiones = sesiones_dia(dia)
    return sesiones[-1] if sesiones else None


def sesion_actual_o_proxima(ahora: datetime) -> tuple[Optional[Sesion], str]:
    """Busca si hay una sesión en curso o la siguiente del día."""
    dia = ahora.weekday()
    hora_actual = ahora.time()
    sesiones = sesiones_dia(dia)
    for s in sesiones:
        if s.inicio <= hora_actual <= s.fin:
            return s, "en_curso"
    for s in sesiones:
        if s.inicio > hora_actual:
            return s, "siguiente"
    return None, "ninguna"


def recordatorio_7min(ahora: Optional[datetime] = None) -> Optional[Sesion]:
    """Devuelve la sesión para la que hay que avisar AHORA (7 min antes)."""
    if ahora is None:
        ahora = datetime.now()

    hoy = ahora.weekday()
    if hoy > 4:
        return None

    objetivo = ahora + timedelta(minutes=7)
    hora_objetivo = objetivo.time()

    for s in HORARIO_COMPLETO:
        if s.dia == hoy:
            # Tolerancia de +/- 1 minuto por si el job se retrasa ligeramente
            diff = (s.inicio.hour * 60 + s.inicio.minute) - (hora_objetivo.hour * 60 + hora_objetivo.minute)
            if abs(diff) <= 1:
                return s
    return None


def icono_asignatura(asig: str) -> str:
    asig_l = sin_tildes(asig.lower())
    if "programac" in asig_l:
        return "💻"
    elif "fisic" in asig_l:
        return "⚡"
    elif "computador" in asig_l:
        return "🖥️"
    elif "algeb" in asig_l:
        return "📐"
    elif "estadist" in asig_l:
        return "📊"
    return "📚"


def formatear_sesion_corta(s: Sesion) -> str:
    icono = icono_asignatura(s.asignatura)
    sub = f" <i>[{s.subgrupo}]</i>" if s.subgrupo else ""
    return f"• <code>{s.inicio.strftime('%H:%M')}-{s.fin.strftime('%H:%M')}</code> {icono} <b>{s.asignatura}</b> ({s.tipo.value}){sub} — 📍 <i>{s.aula}</i>"


def formatear_sesion_destacada(s: Sesion, titulo: str) -> str:
    icono = icono_asignatura(s.asignatura)
    sub = f" [Subgrupo: {s.subgrupo}]" if s.subgrupo else ""
    notas = f"\nℹ️ <i>{s.notas}</i>" if s.notas else ""
    return (
        f"{titulo}:\n\n"
        f"{icono} <b>{s.asignatura}</b> ({s.tipo.value}){sub}\n"
        f"🕑 <b>{s.inicio.strftime('%H:%M')} - {s.fin.strftime('%H:%M')}</b>\n"
        f"📍 <b>Aula:</b> {s.aula}{notas}"
    )


def formatear_dia(dia: int, solo_primera: bool = False, solo_ultima: bool = False, destacar_inicio: bool = True) -> str:
    if dia > 4:
        return f"🏖️ El <b>{DIAS[dia]}</b> no tienes clases (fin de semana)."

    sesiones = sesiones_dia(dia)
    if not sesiones:
        return f"🏖️ El <b>{DIAS[dia]}</b> no tienes clases registradas."

    nombre_dia = DIAS[dia]

    if solo_primera:
        primera = sesiones[0]
        res = formatear_sesion_destacada(primera, f"🌅 <b>{nombre_dia} a primera hora</b>")
        if len(sesiones) > 1:
            res += f"\n\n📋 <b>El resto de tus clases del {nombre_dia}:</b>\n"
            for s in sesiones[1:]:
                res += f"{formatear_sesion_corta(s)}\n"
        return res

    if solo_ultima:
        ultima = sesiones[-1]
        res = formatear_sesion_destacada(ultima, f"🌇 <b>Última clase del {nombre_dia}</b>")
        return res

    lineas = [f"📅 <b>Clases del {nombre_dia}</b> (Grupo B - pB4)\n"]
    for s in sesiones:
        lineas.append(formatear_sesion_corta(s))

    return "\n".join(lineas)


def formatear_semana() -> str:
    lineas = ["📅 <b>Horario Semanal — Grupo B (pB4)</b>\n"]
    for d in range(5):
        lineas.append(f"<b>{DIAS[d]}</b>")
        ses = sesiones_dia(d)
        if ses:
            for s in ses:
                lineas.append(f"  {formatear_sesion_corta(s)}")
        else:
            lineas.append("  <i>Sin clases</i>")
        lineas.append("")
    return "\n".join(lineas)


def responder_consulta(pregunta: str, ahora: Optional[datetime] = None) -> str:
    """Interpreta la duda sobre el horario y genera una respuesta directa y clara."""
    if ahora is None:
        ahora = datetime.now()

    texto = sin_tildes(pregunta.lower())

    # ¿Pide toda la semana?
    if "semana" in texto or "completo" in texto or "todo el horario" in texto:
        return formatear_semana()

    # ¿Pide un día específico?
    dia_objetivo = None
    for nombre_d, num_d in DIAS_BUSQUEDA.items():
        if re.search(rf"\b{nombre_d}\b", texto):
            dia_objetivo = num_d
            break

    # Determinar si pregunta por mañana o hoy
    es_manana = "manana" in texto or "pasado manana" in texto
    es_hoy = "hoy" in texto or "esta tarde" in texto or "esta manana" in texto

    if dia_objetivo is None:
        if "pasado manana" in texto:
            dia_objetivo = (ahora.weekday() + 2) % 7
        elif es_manana:
            dia_objetivo = (ahora.weekday() + 1) % 7
        elif es_hoy:
            dia_objetivo = ahora.weekday()
        elif "ahora" in texto or "siguiente" in texto or "toca" in texto:
            dia_objetivo = ahora.weekday()
        else:
            # Por defecto si son más de las 20:00 o no hay más clases hoy, referirse a mañana
            if ahora.weekday() < 5:
                sesiones_restantes = [s for s in sesiones_dia(ahora.weekday()) if s.fin > ahora.time()]
                if not sesiones_restantes or ahora.hour >= 20:
                    dia_objetivo = (ahora.weekday() + 1) % 7
                else:
                    dia_objetivo = ahora.weekday()
            else:
                dia_objetivo = 0  # Lunes si es finde

    # Filtros de momento del día
    solo_primera = bool(re.search(r"\b(primera hora|primera clase|a que entro|a que hora entro|empezar|empiezo)\b", texto))
    solo_ultima = bool(re.search(r"\b(ultima hora|ultima clase|a que salgo|a que hora salgo|termino|acabo)\b", texto))

    # Si pregunta "¿qué toca ahora / siguiente clase?"
    if ("ahora" in texto or "siguiente" in texto or "proxima" in texto) and dia_objetivo == ahora.weekday():
        ses, estado = sesion_actual_o_proxima(ahora)
        if estado == "en_curso" and ses:
            return (
                f"🔴 <b>Clase en curso ahora mismo:</b>\n\n"
                f"{icono_asignatura(ses.asignatura)} <b>{ses.asignatura}</b> ({ses.tipo.value})\n"
                f"🕑 {ses.inicio.strftime('%H:%M')} - {ses.fin.strftime('%H:%M')}\n"
                f"📍 Aula: <b>{ses.aula}</b>"
            )
        elif estado == "siguiente" and ses:
            diff_min = (ses.inicio.hour * 60 + ses.inicio.minute) - (ahora.hour * 60 + ahora.minute)
            return (
                f"⏳ <b>Tu próxima clase hoy (en ~{diff_min} min):</b>\n\n"
                f"{icono_asignatura(ses.asignatura)} <b>{ses.asignatura}</b> ({ses.tipo.value})\n"
                f"🕑 {ses.inicio.strftime('%H:%M')} - {ses.fin.strftime('%H:%M')}\n"
                f"📍 Aula: <b>{ses.aula}</b>"
            )
        else:
            if ahora.weekday() < 4:
                return f"✅ <b>Ya has terminado todas las clases de hoy.</b>\n\n{formatear_dia((ahora.weekday() + 1) % 7)}"
            else:
                return "✅ <b>Ya has terminado las clases de hoy.</b> ¡Buen fin de semana!"

    return formatear_dia(dia_objetivo, solo_primera=solo_primera, solo_ultima=solo_ultima)
