"""Responder preguntas abiertas cruzando Studium, correo e internet.

"¿Cómo pido el cambio de curso?" no vive en un solo sitio: parte está en
tus tareas de Studium, parte te llegó por correo y el procedimiento oficial
está en la web de la USAL. Este módulo junta las cuatro fuentes y devuelve
una sola respuesta:

- Studium: tus tareas y asignaturas que casan con la pregunta.
- Correo: mensajes guardados que hablan de lo mismo.
- Webs de la USAL: búsqueda acotada a usal.es + lectura de las 2 mejores.
- Internet: búsqueda general como red de seguridad.

Sin conexión o sin resultados web, responde igual con lo local y lo dice.
Con LLM configurado (el mismo de `nlu`), redacta la respuesta; sin él,
monta un parte extractivo con enlaces. Nada aquí rompe nunca: cada fuente
que falla se salta y se cuenta al final.
"""

from __future__ import annotations

import asyncio
import base64
import html as _html
import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlencode

import httpx

from . import db
from . import format as fmt
from .config import cfg
from .texto import VACIAS, palabras, parecido

UA = {"User-Agent": "usal-bot/1.0 (+https://studium.usal.es)"}
ESPERA_WEB = 6.0
MAX_TAREAS = 3
MAX_CORREOS = 3
MAX_ENLACES = 4

# Palabras de pregunta que no distinguen nada ("cómo", "puedo"...).
# `palabras()` ya quita artículos y "curso"/"asignatura"; aquí va lo que
# queda de la forma de preguntar. Sin tildes: `palabras` ya normaliza.
# Verbos de cortesía que en la web solo meten ruido ("puedo" → conjugaciones,
# "veo mis notas" → cámaras Veo). Ojo: aquí SÍ valen "curso", "aplicar" o
# "matrícula", que en local se quitan.
RUIDO_WEB = {
    "como", "puedo", "puede", "pueden", "podria", "hacer", "hago",
    "quiero", "quiere", "tengo", "tiene", "donde", "cuando", "cual",
    "cuales", "sobre", "acerca", "pasos", "forma", "manera", "esto",
    "eso", "este", "esta", "veo", "ves", "ven", "ver", "voy", "vas",
    "van", "dime", "saber",
    # Pegamento gramatical: se colaba en las queries ("que/para" → spam
    # en Bing) y rompía el AND local. Nunca distingue una duda de otra.
    "que", "con", "por", "para", "una", "uno", "unas", "unos",
    "ese", "esa", "esos", "esas", "aquel", "aquella",
    "sus", "mis", "tus", "nos", "les",
    "pero", "pues", "porque", "aunque", "asi", "aqui", "ahi", "alli",
    "ahora", "antes", "despues", "todavia", "tambien", "tampoco",
    "siempre", "nunca", "mucho", "muchos", "poco", "poca",
    "todo", "toda", "todos", "todas",
    "otro", "otra", "otros", "otras", "mismo", "misma",
    "cada", "entre", "hasta", "desde", "tras", "hacia", "durante",
    "quien", "quienes", "cuyo", "cuya",
    "solo", "mas", "muy", "tan",
    # Verbos comodín de preguntar: "irme de Erasmus" lo cuenta "erasmus".
    "irme", "venir", "viene", "vienen",
    "decir", "dice", "dicen", "dar", "dan",
    "salir", "sale", "salen", "poner", "pone", "ponen",
    "quedar", "queda", "quedan",
    "faltar", "falta", "faltan", "tocar", "toca", "tocan",
    "parecer", "parece", "parecen", "tratar", "trata", "tratan",
    "conectar", "conecto", "conecta", "conectan",
    # Verbos de trámite: en la web envenenan la query ("solicito" entierra
    # "beca general ministerio" y Bing devuelve Facebook).
    "solicito", "solicita", "solicitar", "solicitud",
    "aplico", "aplicar", "pido", "pedir",
    # Formas de ser/estar/haber: rompen el AND local ("son las tutorías…").
    "son", "es", "sea", "sean", "fue", "fueron", "eran", "era",
    "hay", "haya", "estan", "estoy", "sido",
}


def palabras_web(pregunta: str) -> list[str]:
    """Claves para buscadores: sin cortesía pero con "curso"/"aplicar"."""
    from .texto import sin_tildes
    utiles = set(VACIAS) - {"curso", "cursos", "grado", "grados"}
    crudas = re.findall(r"[a-z0-9]+", sin_tildes(pregunta))
    claves = [p for p in crudas
              if p not in utiles and p not in RUIDO_WEB
              and (len(p) > 2 or p.isdigit())]
    _con_grupo(pregunta, claves)
    return claves or palabras(pregunta)


RE_CAMBIO_GRUPO = re.compile(
    r"\bdel?\s+(?:la\s+)?[a-z]\s+al?\s+(?:la\s+)?[a-z]\b")


def _con_grupo(pregunta: str, claves: list[str]) -> None:
    """"Del A al B" (letras sueltas) es cambio de grupo aunque no lo diga:
    en Informática hay clase A y B, y "curso" ahí es coloquial de grupo.
    Sin la palabra, Bing entiende divisas y el local no casa "grupo"."""
    from .texto import sin_tildes
    if ("grupo" not in claves and "grupos" not in claves
            and RE_CAMBIO_GRUPO.search(sin_tildes(pregunta.lower()))):
        claves.append("grupo")


# En local sobran además los verbos de trámite: con AND cuantas menos
# palabras, más recuerda ("cambio" encuentra; "aplicar cambio" exige las dos).
# Y "usal"/"universidad"/"salamanca" no distinguen nada: todo lo tuyo es USAL
# (evita que un correo case solo por "Becas USAL").
CORTESIA_LOCAL = RUIDO_WEB | {
    "solicitar", "solicitud", "solicito", "aplicar", "aplico",
    "pedir", "pido", "usal", "universidad", "salamanca",
}


def _raiz(palabra: str) -> str:
    """Singular aproximado para buscar: "tutorías" también casa "tutoría".

    El LIKE es por substring, así que recortar la -s final mantiene el
    match del plural y suma el del singular. Sin milagros con irregulares.
    """
    if len(palabra) > 3 and palabra.endswith("s"):
        return palabra[:-1]
    return palabra


def palabras_clave(pregunta: str) -> list[str]:
    """Lo que de verdad distingue una pregunta de otra.

    "Cómo puedo aplicar el cambio de curso" → ["cambio", ...]:
    se cae la cortesía ("cómo", "puedo"), los artículos y "curso".
    Si no queda nada útil, se devuelve todo antes que buscar vacío.
    """
    todas = palabras(pregunta)
    # Sin duplicados ("grupo A al grupo B" → una vez basta).
    utiles = list(dict.fromkeys(
        p for p in todas if p not in CORTESIA_LOCAL))
    _con_grupo(pregunta, utiles)
    return utiles or todas


# ------------------------------------------------------------------ local
@dataclass
class Local:
    tareas: list = field(default_factory=list)
    correos: list = field(default_factory=list)
    cursos: list = field(default_factory=list)
    claves: list[str] = field(default_factory=list)


def _vale(casas: set[str], n_claves: int) -> bool:
    """Un solo acierto vale si la pregunta es de una palabra o la palabra
    es gorda ("matrícula"); "wifi" solo no cuela cuando se preguntó más."""
    return (len(casas) >= 2 or n_claves <= 1
            or any(len(c) >= 6 for c in casas))


def buscar_local(pregunta: str) -> Local:
    """Lo tuyo: tareas, correos y cursos que casan con la pregunta.

    Tareas y correos puntúan por palabras (cuantas más casen, más arriba)
    en vez de exigirlas todas: el AND dejaba fuera "Matrícula: cita de
    automatrícula" cuando se preguntaba por "plazo de matrícula".
    """
    claves = palabras_clave(pregunta)
    local = Local(claves=claves)
    if not claves:
        return local
    por_tarea: dict[str, list] = {}
    for palabra in dict.fromkeys(claves):
        try:
            halladas = db.tareas_de_curso(_raiz(palabra), limite=MAX_TAREAS)
        except Exception:
            continue
        for t in halladas:
            hueco = por_tarea.setdefault(t.id, [t, set()])
            hueco[1].add(palabra)

    def _vence_antes(t) -> float:
        return t.due if t.due else float("inf")

    candidatas = sorted(
        ([t, casas] for t, casas in por_tarea.values()
         if _vale(casas, len(claves))),
        key=lambda th: (-len(th[1]), _vence_antes(th[0])))
    local.tareas = [t for t, _ in candidatas[:MAX_TAREAS]]
    # `buscar_correos` busca la frase literal, así que se pregunta palabra
    # a palabra (más raíz) y se ordena por nº de coincidencias (`_vale`
    # decide si un solo acierto basta: "matrícula" sí, "wifi" solo no).
    por_correo: dict[str, list] = {}
    for palabra in dict.fromkeys(claves):
        for variante in dict.fromkeys((palabra, _raiz(palabra))):
            try:
                hallados = db.buscar_correos(variante, limite=MAX_CORREOS)
            except Exception:
                continue
            for m in hallados:
                hueco = por_correo.setdefault(m.id, [m, set()])
                hueco[1].add(palabra)
    candidatas = sorted(
        ([m, casas] for m, casas in por_correo.values()
         if _vale(casas, len(claves))),
        key=lambda mc: (-len(mc[1]), -(mc[0].ts or 0)))
    local.correos = [m for m, _ in candidatas[:MAX_CORREOS]]
    consulta = " ".join(dict.fromkeys(_raiz(c) for c in claves))
    try:
        cursos = [c for c in db.cursos()
                  if parecido(consulta, c.name.split("(")[0]) >= 0.5]
        local.cursos = cursos[:2]
    except Exception:
        local.cursos = []
    return local


# --------------------------------------------------------------------- web
@dataclass
class Enlace:
    titulo: str
    url: str
    resumen: str = ""
    de_usal: bool = False


def _es_usal(url: str) -> bool:
    return "usal.es" in (url or "").lower()


async def _ddg_json(consulta: str) -> list[Enlace]:
    """Respuesta instantánea de DuckDuckGo (JSON estable, sin clave)."""
    url = ("https://api.duckduckgo.com/?q=" + quote_plus(consulta) +
           "&format=json&no_html=1&lang=es-es")
    async with httpx.AsyncClient(timeout=ESPERA_WEB, headers=UA) as cli:
        r = await cli.get(url)
        r.raise_for_status()
        datos = r.json()
    enlaces: list[Enlace] = []
    if datos.get("AbstractText") and datos.get("AbstractURL"):
        enlaces.append(Enlace(titulo=(datos.get("Heading") or "Resumen")[:90],
                              url=datos["AbstractURL"],
                              resumen=datos["AbstractText"][:300]))
    for tema in (datos.get("RelatedTopics") or [])[:6]:
        if isinstance(tema, dict) and tema.get("FirstURL") and tema.get("Text"):
            enlaces.append(Enlace(titulo=tema["Text"][:90],
                                  url=tema["FirstURL"],
                                  resumen=tema["Text"][:300]))
    return enlaces


# Bing esconde el destino en un redireccionador (/ck/a?...&u=a1<base64>).
RE_BING_BLOQUE = re.compile(r'<li class="b_algo".*?</li>', re.S)
RE_BING_A = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
RE_BING_TROZO = re.compile(r'<div class="b_caption">.*?<p[^>]*>(.*?)</p>', re.S)
RE_TAG = re.compile(r"<[^>]+>")


def _limpio(html: str) -> str:
    return _html.unescape(RE_TAG.sub("", html or "")).strip()


def _destino_bing(href: str) -> str:
    """El /ck/a de Bing lleva la URL real en base64 (u=a1...)."""
    href = _html.unescape(href or "")
    m = re.search(r"[?&]u=a1([A-Za-z0-9+/=_-]+)", href)
    if m:
        try:
            real = base64.urlsafe_b64decode(m.group(1) + "==").decode("utf-8", "ignore")
            if real.startswith("http"):
                return real
        except Exception:
            pass
    return href if href.startswith("http") else ""


def _parsear_bing(cuerpo: str) -> list[Enlace]:
    """Bloques b_algo → enlaces, decodificando el redireccionador."""
    enlaces: list[Enlace] = []
    for bloque in RE_BING_BLOQUE.findall(cuerpo or "")[:8]:
        m = RE_BING_A.search(bloque)
        if not m:
            continue
        destino = _destino_bing(m.group(1))
        if not destino:
            continue
        titulo = _limpio(m.group(2))[:90] or destino
        trozo = RE_BING_TROZO.search(bloque)
        resumen = _limpio(trozo.group(1))[:300] if trozo else ""
        enlaces.append(Enlace(titulo=titulo, url=destino, resumen=resumen))
    return enlaces


async def _bing(consulta: str) -> list[Enlace]:
    """HTML de Bing: sin clave y aguanta scripts. Lista vacía si no hay nada."""
    url = "https://www.bing.com/search?" + urlencode({"q": consulta, "setlang": "es"})
    async with httpx.AsyncClient(timeout=ESPERA_WEB, headers=UA,
                                 follow_redirects=True) as cli:
        r = await cli.get(url)
        r.raise_for_status()
        return _parsear_bing(r.text)


# El buscador interno de la USAL (Drupal): GET /search/node/<claves>.
RE_USAL_A = re.compile(r'<h[23][^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def _parsear_usal(cuerpo: str) -> list[Enlace]:
    """Titulares del Drupal de la USAL → enlaces absolutos."""
    enlaces: list[Enlace] = []
    for href, titulo in RE_USAL_A.findall(cuerpo or ""):
        href = (href or "").strip()
        if not href or href.startswith("/search/node"):
            continue
        if href.startswith("/"):
            href = "https://usal.es" + href
        if not href.startswith("http"):
            continue
        titulo = _limpio(titulo)[:90] or href
        enlaces.append(Enlace(titulo=titulo, url=href, resumen="",
                              de_usal=True))
        if len(enlaces) >= MAX_ENLACES:
            break
    return enlaces


async def _usal(claves: str) -> list[Enlace] | None:
    """Lo que la propia USAL indexa de esas palabras. None si peta la red."""
    url = "https://usal.es/search/node/" + quote_plus(claves[:120])
    try:
        async with httpx.AsyncClient(timeout=ESPERA_WEB, headers=UA,
                                     follow_redirects=True) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            return _parsear_usal(r.text)
    except Exception:
        return None


async def _buscar(consulta: str) -> list[Enlace] | None:
    """Enlaces, [] si no hay nada, None si petó la red del todo."""
    try:
        enlaces = await _bing(consulta)
    except Exception:
        enlaces = None
    if enlaces:
        return enlaces
    try:
        return await _ddg_json(consulta)
    except Exception:
        # Si Bing contestó aunque fuera vacío, es "sin resultados", no caída.
        return [] if enlaces is not None else None


async def _seguro(coro) -> list[Enlace] | None:
    """Una fuente web que peta devuelve None en vez de tumbar al resto."""
    try:
        return await coro
    except Exception:
        return None


def _titulo_base(titulo: str) -> str:
    """Para no citar tres veces lo mismo ("Enología…" 2023/2024/2025)."""
    base = re.sub(r"[^a-z0-9áéíóúñü]+", " ", (titulo or "").lower())
    return re.sub(r"\b(19|20)\d{2}\b", " ", base).strip()


def _pertinente(e: "Enlace", claves: list[str]) -> bool:
    """El enlace habla de lo preguntado: comparte palabra de peso.

    Frena el spam de Bing (letras de canciones, Facebook, Outlook) sin
    tocar la pata USAL, que ya viene del índice de la propia universidad.
    """
    texto = f"{e.titulo} {e.resumen}".lower()
    gordas = [c for c in claves if len(c) >= 6]
    if any(c in texto for c in gordas):
        return True
    finas = [c for c in claves if len(c) > 3]
    return sum(1 for c in finas if c in texto) >= 2


def _puntos(e: "Enlace", claves: list[str]) -> int:
    """Relevancia: cada palabra gorda (6+) vale 2, cada fina (4-5) vale 1."""
    texto = f"{e.titulo} {e.resumen} {e.url}".lower()
    puntos = 0
    for c in claves:
        if len(c) >= 6:
            puntos += 2 if c in texto else 0
        elif len(c) > 3:
            puntos += 1 if c in texto else 0
    return puntos


def _distintas(e: "Enlace", claves: list[str]) -> int:
    """Cuántas claves (de 4+ letras) aparecen en el enlace."""
    texto = f"{e.titulo} {e.resumen} {e.url}".lower()
    return sum(1 for c in claves if len(c) > 3 and c in texto)


def _ancla(e: "Enlace", claves: list[str]) -> bool:
    """La única coincidencia es una palabra larga y distintiva
    ("bibliotecas") y no una polisémica corta ("cambio", "curso"):
    eso no es spam aunque venga sola."""
    texto = f"{e.titulo} {e.resumen} {e.url}".lower()
    return any(len(c) >= 8 and c in texto for c in claves)


async def _vivos(enlaces: list[Enlace]) -> list[Enlace]:
    """Saca los enlaces muertos: citar un caído (URJC) no vale como fuente.
    Solo caen los errores de red/timeout; un 403 (anti-bots) o 404 se
    queda: responde algo, no está muerto."""
    if not enlaces:
        return enlaces

    async def _vive(cli: "httpx.AsyncClient", e: Enlace) -> Enlace | None:
        try:
            await cli.head(e.url)
            return e
        except Exception:
            return None

    try:
        async with httpx.AsyncClient(
                follow_redirects=True, timeout=6.0,
                headers={"User-Agent": "Mozilla/5.0 (usal-bot)"}) as cli:
            res = await asyncio.gather(*(_vive(cli, e) for e in enlaces))
    except Exception:
        return enlaces
    return [e for e in res if e is not None]


async def buscar_web(pregunta: str) -> tuple[list[Enlace], list[Enlace], bool]:
    """(enlaces USAL, enlaces generales, web_caida). Nunca levanta.

    La pata USAL sale del buscador interno de usal.es (lo que la propia
    universidad indexa); la general, de Bing. Lo de usal.es que cuele en
    Bing no se repite dos veces.
    """
    web = palabras_web(pregunta)[:6]
    q_gen = (" ".join(web) + " universidad").strip() or pregunta[:120]
    # Lo específico manda: Bing ignora lo raro si la query lleva palabras
    # comunes ("conectar wifi eduroam" → ayuda de Windows; "conecto eduroam"
    # → spam en chino). Se pregunta también por la palabra más larga sola
    # ("eduroam universidad" sí acierta).
    largas = sorted([c for c in web if len(c) >= 6 and c != "universidad"],
                    key=len, reverse=True)
    focos = [f"{c} universidad" for c in largas[:2]]
    busquedas = [_seguro(_usal(" ".join(web))), _seguro(_buscar(q_gen))]
    for foco in focos:
        if foco != q_gen:
            busquedas.append(_seguro(_buscar(foco)))
    # La palabra más distintiva, sola, al índice USAL: el Drupal ordena
    # por sus criterios y a veces entierra lo específico ("expediente"
    # solo sí saca el traslado de expediente).
    solo = largas[0] if largas else ""
    if solo:
        busquedas.append(_seguro(_usal(solo)))
    todo = await asyncio.wait_for(asyncio.gather(*busquedas),
                                  timeout=ESPERA_WEB * 2 + 4)
    res_usal, res_gen = todo[0], todo[1]
    resto = todo[2:-1] if solo else todo[2:]
    for extra in resto:
        # None es "petó la red": no convierte un None en lista vacía,
        # o la caída dejaría de detectarse.
        if extra is None:
            continue
        res_gen = (res_gen or []) + extra
    if solo and todo[-1]:
        res_usal = (res_usal or []) + todo[-1]
    caida = all(r is None for r in todo)
    web = palabras_web(pregunta)
    usal: list[Enlace] = []
    vistos_url: set[str] = set()
    vistos_tit: set[str] = set()
    varias = sum(1 for c in web if len(c) > 3) >= 2
    for e in (res_usal or []) + [x for x in (res_gen or []) if _es_usal(x.url)]:
        # El índice USAL también cuela ruido ("erasmus" → coronavirus):
        # sin palabra de peso en el titular, fuera. Y con 2+ claves hay
        # que casar 2 igual que en lo general ("cambio" solo → enología),
        # salvo que la única coincidencia sea distintiva ("bibliotecas").
        if not _pertinente(e, web):
            continue
        if varias and _distintas(e, web) < 2 and not _ancla(e, web):
            continue
        base = _titulo_base(e.titulo)
        if e.url in vistos_url or (base and base in vistos_tit):
            continue
        vistos_url.add(e.url)
        if base:
            vistos_tit.add(base)
        e.de_usal = True
        usal.append(e)
        if len(usal) >= MAX_ENLACES:
            break
    puntuados: list[tuple[int, Enlace]] = []
    for e in (res_gen or []):
        # La query de foco puede repetir lo de la general: sin duplicados.
        # Y con 2+ claves hay que casar 2: Bing posiciona spam que solo
        # casa la polisémica ("cambio" → divisas, "matrícula" → coches).
        # La pata USAL queda fuera: es dominio oficial, no SEO.
        if e.url in vistos_url or not _pertinente(e, web):
            continue
        if varias and _distintas(e, web) < 2 and not _ancla(e, web):
            continue
        vistos_url.add(e.url)
        puntuados.append((_puntos(e, web), e))
    # Lo que casa con más palabras, primero: "notas + expediente" gana
    # a "expediente" solo (adiós, extranjería cuando hay algo mejor).
    puntuados.sort(key=lambda pe: (-pe[0], pe[1].url))
    general = [e for _, e in puntuados[:MAX_ENLACES]]
    # Sin citar muertos: lo local (Studium/Gmail) no se toca, es tuyo.
    usal = await _vivos(usal)
    general = await _vivos(general)
    return usal, general, caida


RE_SCRIPT = re.compile(
    r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", re.S | re.I)


async def leer_pagina(url: str, max_chars: int = 1200) -> str:
    """El texto de una página, sin menús ni código. Vacío si falla."""
    try:
        async with httpx.AsyncClient(timeout=ESPERA_WEB, headers=UA,
                                     follow_redirects=True) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            cuerpo = r.text[:30000]
    except Exception:
        return ""
    cuerpo = RE_SCRIPT.sub(" ", cuerpo)
    texto = _html.unescape(RE_TAG.sub(" ", cuerpo))
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:max_chars]


# ------------------------------------------------------------- redacción
SISTEMA = (
    "Eres el asistente universitario del usuario (Universidad de Salamanca). "
    "Responde en español, 150 palabras como mucho, basándote SOLO en el "
    "contexto que se te da. Di de qué fuente sale cada dato: Studium, "
    "correo, USAL o internet. Si el contexto no trae la respuesta, dilo y "
    "recomienda comprobarlo en la web oficial de la USAL. Nada de inventar "
    "plazos ni requisitos."
)


def _con_enlace(titulo: str, url: str) -> str:
    """Título con enlace directo si hay URL; si no, título pelado."""
    titulo = _html.escape(titulo or url or "(sin título)")
    if url:
        return (f"<a href=\"{_html.escape(url, quote=True)}\">"
                f"{titulo}</a>")
    return titulo


def _bloque_fuentes(local: Local, usal: list[Enlace],
                    general: list[Enlace]) -> str:
    """De dónde sale cada dato, con enlace directo a la fuente.

    Lo web enlaza a la página; lo local enlaza a Studium/Gmail cuando el
    dato trae URL (si no, se cita el origen sin enlace: no hay a dónde).
    """
    lineas = ["\n\n🔗 <b>Fuentes</b>"]
    for t in local.tareas[:MAX_TAREAS]:
        lineas.append(f"• 📚 {_con_enlace(t.title, t.url)}")
    for m in local.correos[:MAX_CORREOS]:
        lineas.append(f"• 📬 {_con_enlace(m.subject, m.url)}")
    for e in (usal + general)[:MAX_ENLACES]:
        lineas.append(f"• 🌐 {_con_enlace(e.titulo or e.url, e.url)}")
    return "\n".join(lineas) if len(lineas) > 1 else ""


async def _con_modelo(pregunta: str, contexto: str) -> str | None:
    """Redactar con el LLM ya configurado. None si no hay o si falla."""
    if not cfg.llm_enabled or not contexto.strip():
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as cli:
            r = await cli.post(
                f"{cfg.llm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {cfg.llm_api_key}"},
                json={
                    "model": cfg.llm_model,
                    "temperature": 0.2,
                    "max_tokens": 400,
                    "chat_template_kwargs": {"thinking": False},
                    "messages": [
                        {"role": "system", "content": SISTEMA},
                        {"role": "user", "content":
                         f"Pregunta: {pregunta[:300]}\n\nContexto:\n{contexto[:4000]}"},
                    ],
                },
            )
            r.raise_for_status()
            mensaje = r.json()["choices"][0]["message"]
            texto = ((mensaje.get("content") or "")
                     or (mensaje.get("reasoning_content") or "")).strip()
            return texto or None
    except Exception:
        return None


def _vence(t) -> str:
    """La fecha límite en claro: el modelo la ignoraba si iba en jerga."""
    try:
        dt = t.due_dt
        return dt.strftime("%d de %B") if dt else "sin fecha a la vista"
    except Exception:
        return "sin fecha a la vista"


def _contexto_para_modelo(local: Local, usal: list[Enlace],
                           general: list[Enlace],
                           extractos: list[str]) -> str:
    lineas = []
    for t in local.tareas[:MAX_TAREAS]:
        lineas.append(f"[Studium] {t.title} ({t.course or 'sin curso'})"
                      f" — vence: {_vence(t)}")
    for m in local.correos[:MAX_CORREOS]:
        lineas.append(f"[Correo] {m.subject} — {(m.snippet or '')[:200]}")
    for e in usal[:3]:
        lineas.append(f"[USAL] {e.titulo}: {e.resumen[:200]} ({e.url})")
    for txt in extractos[:2]:
        lineas.append(f"[Web USAL] {txt[:500]}")
    for e in general[:2]:
        lineas.append(f"[Internet] {e.titulo}: {e.resumen[:200]} ({e.url})")
    return "\n".join(lineas)


def _plantilla(pregunta: str, local: Local, usal: list[Enlace],
               general: list[Enlace], web_caida: bool) -> str:
    """El parte extractivo: lo local formateado + enlaces. Sin modelo."""
    partes = [f"❓ <b>{_html.escape(pregunta[:160])}</b>"]
    if local.tareas:
        partes.append(fmt.lista_tareas(
            local.tareas, "📚 En tu Studium"))
    elif local.cursos:
        nombres = ", ".join(_html.escape(c.name.split('(')[0].strip())
                            for c in local.cursos)
        partes.append(f"📚 <b>En tu Studium</b>\n• Tienes {nombres}, "
                      f"pero nada con «{_html.escape(' '.join(local.claves[:3]))}».")
    else:
        partes.append("📚 <b>En tu Studium</b>\n• Nada con esas palabras.")
    if local.correos:
        partes.append(fmt.lista_correos(
            local.correos, "📬 En tu correo"))
    else:
        partes.append("📬 <b>En tu correo</b>\n• Nada con esas palabras.")
    enlaces = usal + [e for e in general if e.url not in {u.url for u in usal}]
    if enlaces:
        lineas = ["🌐 <b>En la USAL e internet</b>"]
        for e in enlaces[:MAX_ENLACES]:
            titulo = _html.escape(e.titulo or e.url)
            url = _html.escape(e.url, quote=True)
            linea = f"• <a href=\"{url}\">{titulo}</a>"
            if e.resumen:
                linea += f" — {_html.escape(e.resumen[:160])}"
            lineas.append(linea)
        partes.append("\n".join(lineas))
    elif web_caida:
        partes.append("🌐 <b>Internet</b>\n• No he podido mirarlo ahora mismo "
                      "(¿sin conexión?). Lo de arriba es lo que tienes en casa.")
    else:
        partes.append("🌐 <b>Internet</b>\n• Sin resultados para esas palabras.")
    # El aviso de plazos solo pinta algo cuando hay datos personales o
    # trámite detrás; para el mundial o el wifi sobra.
    if local.tareas or local.correos:
        partes.append("<i>Comprueba plazos y requisitos en la web oficial: "
                     "cambian cada curso.</i>")
    return "\n\n".join(partes)


async def responder_pregunta(pregunta: str) -> str:
    """Juntar las cuatro fuentes en una sola respuesta (HTML de Telegram)."""
    pregunta = (pregunta or "").strip()
    if not pregunta:
        return ("Dime la duda entera: <code>/pregunta cómo pido el cambio "
                "de curso</code>")
    local = buscar_local(pregunta)
    try:
        usal, general, web_caida = await asyncio.wait_for(
            buscar_web(pregunta), timeout=ESPERA_WEB * 2 + 4)
    except Exception:
        usal, general, web_caida = [], [], True
    extractos: list[str] = []
    for e in usal[:2]:
        try:
            txt = await asyncio.wait_for(leer_pagina(e.url), timeout=8)
        except Exception:
            txt = ""
        if txt:
            extractos.append(txt)
    contexto = _contexto_para_modelo(local, usal, general, extractos)
    redactada = await _con_modelo(pregunta, contexto)
    if redactada:
        resp = f"❓ <b>{_html.escape(pregunta[:160])}</b>\n\n{_html.escape(redactada)}"
        return resp + _bloque_fuentes(local, usal, general)
    return _plantilla(pregunta, local, usal, general, web_caida)
