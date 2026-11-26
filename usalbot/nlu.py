"""Entender lo que le preguntas en cristiano, sin comandos.

Primero lo intenta con reglas, que son instantaneas, gratis y predecibles.
Solo si las reglas no ven nada claro (y tienes configurado LLM_API_KEY) le
pregunta a un modelo. Sin modelo configurado el bot sigue funcionando: las
reglas cubren la forma normal de preguntar estas cosas.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from .config import cfg
from .texto import sin_tildes

INTENCIONES = [
    "proximas", "hoy", "manana", "semana", "urgente", "asignatura", "asignaturas",
    "correos", "correos_clave", "buscar", "cuando", "resumen", "ayuda",
    "aplazar", "entregadas", "pregunta", "horario",
]


@dataclass
class Intencion:
    nombre: str = "proximas"
    filtro: str = ""      # nombre de asignatura o texto a buscar
    dias: int = 0         # si la pregunta acota un plazo
    fuente: str = "reglas"


# Cada regla: (patron, intencion). Se prueban en orden, gana la primera.
REGLAS: list[tuple[str, str]] = [
    (r"\b(ayuda|que sabes hacer|como funciona|comandos)\b", "ayuda"),
    (r"\b(resumen|como voy|que tal voy|ponme al dia|situacion)\b", "resumen"),
    (r"\b(mis )?(asignaturas|matriculas|cursos|materias)\b(?!.*\btarea)", "asignaturas"),
    (r"\b(correos? (importantes?|clave|que importan)|algo importante|me he perdido)\b", "correos_clave"),
    (r"\b(correos?|emails?|mails?|buzon|bandeja|mensajes del correo)\b", "correos"),
    (r"\b(busca|buscar|encuentra|hay algo (de|sobre)|sobre)\b", "buscar"),
    # Horario y clases van antes que hoy/manana/cuando para no confundir clases con entregas
    (r"\b(clases?|horarios?|aulas?|primera hora|segunda hora|tercera hora|ultima hora|"
     r"que me toca|que toca|a que entro|a que salgo|a que hora entro|a que hora salgo|"
     r"siguiente clase|proxima clase|tengo clase|hay clase)\b", "horario"),
    (r"\b(cuando|que dia|a que hora|fecha limite|deadline|se entrega|hay que entregar)\b", "cuando"),
    (r"\b(hoy|para hoy|esta tarde|esta noche)\b", "hoy"),
    (r"\b(manana)\b", "manana"),
    (r"\b(esta semana|semana|los proximos (7|siete) dias|agenda)\b", "semana"),
    (r"\b(urgente|lo mas urgente|lo primero|corre prisa|que hago (ahora|primero)|prioridad)\b", "urgente"),
    (r"\b(aplaza\w*|pospon\w*|snooze)\b", "aplazar"),
    (r"\b(entregad\w*|terminad\w*|ocultas?|desaparecidas?)\b", "entregadas"),
    # Duda abierta (procedimientos, plazos, "cómo pido X"): va antes que el
    # cajón de sastre de "proximas" para que no se la trague. Lo concreto
    # ("mis matrículas", "busca X") sigue ganando porque va antes.
    (r"\b(como (puedo|hago|se|podria|solicito)|que tengo que hacer|"
     r"que hay que hacer|donde (esta|puedo|se|hay)|cual es el "
     r"(plazo|procedimiento|requisito|proceso)|cambio de "
     r"(curso|grupo|titulacion|turno)|traslado de expediente|"
     r"convocatoria|matricula|requisito)\b", "pregunta"),
    (r"\b(que tengo|que hay|que queda|que me queda|que falta|tengo algo|"
     r"queda algo|dime lo|avisame|cuentame|ensename|muestrame|recuerdame|"
     r"tareas?|entregas?|pendiente|"
     r"pendientes|deberes|trabajos?|practicas?|examenes?|proxim)\b", "proximas"),
]

RE_DIAS = re.compile(r"\b(?:proximos?|siguientes?|en)\s+(\d{1,2})\s*d[ií]as?\b")
RE_ASIGNATURA = re.compile(
    r"\b(?:de|en|para|sobre|con)\s+([a-z0-9ñ][\w ñ.-]{2,40}?)"
    r"(?:\?|$|\s+(?:tengo|hay|queda|para|cuando|que))"
)

# Palabras que nunca son una asignatura aunque vayan detras de "de".
PARADA = {
    "hoy", "manana", "esta", "semana", "mes", "correo", "correos", "email", "emails",
    "tarea", "tareas", "entrega", "entregas", "nuevo", "nuevos", "clase", "clases",
    "plazo", "plazos", "urgente", "todo", "algo", "nada", "que", "cuando", "studium",
    "la", "el", "los", "las", "un", "una", "mi", "mis", "eso", "esto",
    # Fechas genéricas: "la tarea con la fecha más próxima" no es la
    # asignatura "fecha mas proxima", es pedir pendientes.
    "fecha", "fechas", "proxima", "proximo", "proximas", "proximos",
    "mas", "siguiente", "siguientes", "limite",
}


def _extraer_filtro(texto: str) -> str:
    m = RE_ASIGNATURA.search(texto)
    if not m:
        return ""
    bruto = m.group(1).strip(" .?¿!¡")
    palabras = [p for p in bruto.split() if p not in PARADA]
    return " ".join(palabras).strip()


def por_reglas(pregunta: str) -> Intencion | None:
    # Los signos de apertura del castellano estorban a todos los patrones que
    # anclan al principio de la frase: "¿cuando se entrega X" no empezaba por
    # "cuando", asi que el filtro se quedaba con la pregunta entera.
    texto = sin_tildes(pregunta).strip().lstrip("¿¡ ").strip()
    if not texto:
        return None

    dias = 0
    if (m := RE_DIAS.search(texto)):
        dias = int(m.group(1))

    for patron, nombre in REGLAS:
        if re.search(patron, texto):
            filtro = _extraer_filtro(texto)
            if nombre in {"buscar", "cuando"} and not filtro:
                # "busca X" / "cuando es X" sin preposicion delante
                resto = re.sub(
                    r"^\s*(busca(r)?|encuentra|cuando (es|se entrega|hay que entregar)|"
                    r"que dia (es)?|a que hora (es)?)\s*", "", texto
                ).strip(" ?¿.!")
                # Quitar tambien aqui las palabras que no distinguen nada:
                # "la practica 4" tiene que quedarse en "practica 4".
                utiles = [x for x in resto.split() if x not in PARADA]
                filtro = " ".join(utiles) if utiles else ""
            if nombre == "proximas" and filtro:
                nombre = "asignatura"
            return Intencion(nombre=nombre, filtro=filtro, dias=dias)
    return None


SISTEMA = (
    "Eres el analizador de intenciones de un bot universitario. "
    "Devuelve SOLO un JSON con las claves: intent, filter, days. "
    f"intent debe ser uno de: {', '.join(INTENCIONES)}. "
    "filter es el nombre de asignatura o el texto a buscar (cadena vacia si no hay). "
    "days es un numero de dias si la pregunta acota un plazo, si no 0. "
    "Nada de explicaciones ni markdown."
)


# El endpoint gratuito de NVIDIA se cuelga a menudo. Medido: cuando contesta,
# tarda ~400 ms; cuando no, se queda colgado un minuto largo. Asi que se le da
# poco margen y, si no contesta, se responde con las reglas y ya esta: mas vale
# una respuesta generica al momento que un bot mudo diez segundos.
ESPERA = 5.0
INTENTOS = 1


async def por_modelo(pregunta: str) -> Intencion | None:
    if not cfg.llm_enabled:
        return None
    for intento in range(INTENTOS):
        salida = await _una_consulta(pregunta)
        if salida is not None:
            return salida
    return None


async def _una_consulta(pregunta: str) -> Intencion | None:
    try:
        async with httpx.AsyncClient(timeout=ESPERA) as cli:
            r = await cli.post(
                f"{cfg.llm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
                json={
                    "model": cfg.llm_model,
                    "temperature": 0,
                    "max_tokens": 200,
                    # Sin esto, un modelo de razonamiento se pone a pensar en
                    # voz alta: medido con deepseek-v4-flash, pasa de 326 ms a
                    # mas de 90 segundos y se queda sin tokens antes de
                    # contestar. Para decidir si preguntas por hoy o por la
                    # semana no hace falta pensar tanto.
                    "chat_template_kwargs": {"thinking": False},
                    "messages": [
                        {"role": "system", "content": SISTEMA},
                        # Para saber QUE se esta preguntando sobra con el
                        # principio; mandar 4000 caracteres es tirar tiempo.
                        {"role": "user", "content": pregunta[:300]},
                    ],
                },
            )
            r.raise_for_status()
            mensaje = r.json()["choices"][0]["message"]
            # Algunos modelos dejan 'content' vacio y escriben en
            # 'reasoning_content'; el JSON puede estar en cualquiera de los dos.
            crudo = (mensaje.get("content") or "") or (mensaje.get("reasoning_content") or "")
    except Exception:
        return None

    if not crudo:
        return None

    # El ultimo objeto JSON del texto: si el modelo se explica antes, la
    # respuesta buena es la de despues.
    candidatos = re.findall(r"\{[^{}]*\}", crudo, re.S)
    datos = None
    for trozo in reversed(candidatos):
        try:
            posible = json.loads(trozo)
        except ValueError:
            continue
        if isinstance(posible, dict) and "intent" in posible:
            datos = posible
            break
    if datos is None:
        return None

    intent = str(datos.get("intent", "")).strip()
    if intent not in INTENCIONES:
        return None
    return Intencion(
        nombre=intent,
        filtro=str(datos.get("filter") or "").strip(),
        dias=int(datos.get("days") or 0),
        fuente="modelo",
    )


async def entender(pregunta: str) -> Intencion:
    """Reglas primero; el modelo solo para lo que ellas no ven."""
    # Lo que nadie reconoce es duda abierta, no tareas: antes "dime
    # bibliotecas cercanas a mi facultad" caía en el cajón de sastre de
    # "proximas" y devolvía la lista de pendientes. La pregunta abierta
    # también mira tus tareas, así que lo de verdad pendiente sigue
    # apareciendo cuando toca.
    return (por_reglas(pregunta)
            or await por_modelo(pregunta)
            or Intencion(nombre="pregunta", fuente="por defecto"))
