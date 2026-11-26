"""Lectura del correo. Dos caminos, misma salida.

El correo de la USAL esta en Google Workspace, asi que lo normal es usar la
API de Gmail con OAuth (una autorizacion en el navegador y ya). Si prefieres
no tocar Google Cloud, queda IMAP con usuario y contrasena.

En los dos casos se lee, nunca se escribe: el bot no envia ni borra nada.
"""

from __future__ import annotations

import asyncio
import base64
import email
import email.utils
import html
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header

from ..config import cfg
from ..models import Mail

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Lo que viene de la universidad se busca aparte y con mas margen de dias: es
# justo lo que no se puede perder, y si va en el mismo saco que el resto se
# queda fuera en cuanto hay una semana con mucho correo.
CONSULTA_USAL = "(from:usal.es OR to:usal.es OR from:studium OR subject:studium)"
RUIDO_GMAIL = "-category:promotions -category:social"

# De cuantos correos de la USAL vale la pena bajarse el cuerpo entero.
CUERPOS_COMPLETOS = 20


class MailError(RuntimeError):
    pass


def _partir_remitente(bruto: str) -> tuple[str, str]:
    nombre, correo = email.utils.parseaddr(bruto or "")
    return (nombre or correo.split("@")[0]).strip(' "'), correo.lower().strip()


def _limpiar(texto: str) -> str:
    """Gmail devuelve el fragmento con entidades (&#39;), aqui se deshacen."""
    return re.sub(r"\s+", " ", html.unescape(texto or "")).strip()


def _sin_etiquetas(bruto: str) -> str:
    sin = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", bruto, flags=re.S | re.I)
    return _limpiar(re.sub(r"<[^>]+>", " ", sin))


def es_de_la_usal(correo: str) -> bool:
    dominio = correo.rsplit("@", 1)[-1].lower()
    return dominio == "usal.es" or dominio.endswith(".usal.es")


# ------------------------------------------------------------------- Gmail
def _credenciales():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not cfg.gmail_token_file.exists():
        raise MailError(
            "Falta la autorizacion de Gmail. Ejecuta: python setup_wizard.py gmail"
        )
    creds = Credentials.from_authorized_user_file(str(cfg.gmail_token_file), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            cfg.gmail_token_file.write_text(creds.to_json())
        else:
            raise MailError("La autorizacion de Gmail ya no vale. Repite: python setup_wizard.py gmail")
    return creds


def _texto_del_cuerpo(payload: dict) -> str:
    """Saca el texto de un mensaje, que puede venir en varias capas y en base64."""
    def datos(parte: dict) -> str:
        bruto = (parte.get("body") or {}).get("data")
        if not bruto:
            return ""
        try:
            return base64.urlsafe_b64decode(bruto + "===").decode("utf-8", "replace")
        except Exception:
            return ""

    planos, htmls = [], []

    def recorrer(parte: dict, hondura: int = 0) -> None:
        if hondura > 8:
            return
        tipo = parte.get("mimeType", "")
        if tipo == "text/plain":
            planos.append(datos(parte))
        elif tipo == "text/html":
            htmls.append(datos(parte))
        for hija in parte.get("parts", []) or []:
            recorrer(hija, hondura + 1)

    recorrer(payload)
    if planos:
        return _limpiar(" ".join(planos))
    return _sin_etiquetas(" ".join(htmls))


def _adjuntos_de(payload: dict) -> tuple[str, ...]:
    nombres: list[str] = []

    def recorrer(parte: dict, hondura: int = 0) -> None:
        if hondura > 8:
            return
        nombre = parte.get("filename") or ""
        if nombre and (parte.get("body") or {}).get("attachmentId"):
            nombres.append(nombre)
        for hija in parte.get("parts", []) or []:
            recorrer(hija, hondura + 1)

    recorrer(payload)
    return tuple(nombres[:6])


def _gmail_sincrono(dias: int, maximo: int) -> list[Mail]:
    from googleapiclient.discovery import build

    servicio = build("gmail", "v1", credentials=_credenciales(), cache_discovery=False)

    def listar(consulta: str, tope: int) -> list[str]:
        try:
            r = servicio.users().messages().list(
                userId="me", q=consulta, maxResults=tope).execute()
        except Exception:
            return []
        return [m["id"] for m in r.get("messages", [])]

    # Primero lo de la universidad, con el doble de margen de dias: un correo
    # de hace diez dias diciendo que la entrega se amplia sigue importando.
    de_la_usal = listar(f"{CONSULTA_USAL} newer_than:{dias * 2}d", maximo)
    resto = listar(f"newer_than:{dias}d {RUIDO_GMAIL}", maximo)

    orden: list[tuple[str, bool]] = [(i, True) for i in de_la_usal]
    vistos = set(de_la_usal)
    orden += [(i, False) for i in resto if i not in vistos]

    salida: list[Mail] = []
    cuerpos_pedidos = 0
    for mid, de_usal in orden:
        # El cuerpo entero solo para los de la USAL: el fragmento corto a veces
        # es solo el saludo o el pie, y con un asunto vago ("Reunion importante")
        # el correo se colaria sin que nadie lo marcara como importante.
        quiero_cuerpo = de_usal and cuerpos_pedidos < CUERPOS_COMPLETOS
        try:
            if quiero_cuerpo:
                m = servicio.users().messages().get(userId="me", id=mid, format="full").execute()
                cuerpos_pedidos += 1
            else:
                m = servicio.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "Subject", "Date", "To"]).execute()
        except Exception:
            continue

        payload = m.get("payload", {})
        cabeceras = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        nombre, correo = _partir_remitente(cabeceras.get("from", ""))
        asunto = str(make_header(decode_header(cabeceras.get("subject", "(sin asunto)"))))

        cuerpo = _texto_del_cuerpo(payload) if quiero_cuerpo else ""
        fragmento = cuerpo or _limpiar(m.get("snippet", ""))

        salida.append(Mail(
            id=f"gmail:{m['id']}",
            subject=_limpiar(asunto),
            sender=nombre,
            sender_email=correo,
            snippet=fragmento[:1500],
            ts=int(m.get("internalDate", "0")) // 1000,
            url=f"https://mail.google.com/mail/u/0/#all/{m['id']}",
            unread="UNREAD" in m.get("labelIds", []),
            adjuntos=_adjuntos_de(payload) if quiero_cuerpo else (),
        ))
    return salida


# -------------------------------------------------------------------- IMAP
def _imap_sincrono(dias: int, maximo: int) -> list[Mail]:
    if not cfg.imap_user or not cfg.imap_password:
        raise MailError("Faltan IMAP_USER / IMAP_PASSWORD en el .env")

    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%d-%b-%Y")
    servidor = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        servidor.login(cfg.imap_user, cfg.imap_password)
        servidor.select("INBOX", readonly=True)
        _, datos = servidor.search(None, f'(SINCE "{desde}")')
        ids = datos[0].split()[-maximo:]
        _, no_leidos = servidor.search(None, "(UNSEEN)")
        sin_leer = set(no_leidos[0].split())

        salida: list[Mail] = []
        for uid in reversed(ids):
            _, bruto = servidor.fetch(
                uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)] "
                     "BODY.PEEK[TEXT]<0.2000>)")
            if not bruto or not isinstance(bruto[0], tuple):
                continue
            msg = email.message_from_bytes(bruto[0][1])
            # El segundo trozo de la respuesta es el principio del cuerpo, que
            # el triaje necesita para no juzgar solo por el asunto.
            crudo = b""
            for parte in bruto:
                if isinstance(parte, tuple) and parte is not bruto[0]:
                    crudo = parte[1]
            texto = crudo.decode("utf-8", "replace") if crudo else ""
            fragmento = _sin_etiquetas(texto) if "<" in texto else _limpiar(texto)
            nombre, correo = _partir_remitente(msg.get("From", ""))
            fecha = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
            mid = (msg.get("Message-ID") or uid.decode()).strip("<> ")
            salida.append(Mail(
                id=f"imap:{mid}",
                subject=_limpiar(str(make_header(decode_header(msg.get("Subject", "(sin asunto)"))))),
                sender=nombre,
                sender_email=correo,
                snippet=fragmento[:1500],
                ts=int(fecha.timestamp()) if fecha else 0,
                url="",
                unread=uid in sin_leer,
            ))
        return salida
    finally:
        try:
            servidor.logout()
        except Exception:
            pass


# ------------------------------------------------------------------ comun
async def recientes(dias: int = 14, maximo: int = 40) -> list[Mail]:
    """Los correos de los ultimos dias, ya normalizados."""
    if cfg.mail_backend == "none":
        return []
    if cfg.mail_backend == "gmail":
        return await asyncio.to_thread(_gmail_sincrono, dias, maximo)
    if cfg.mail_backend == "imap":
        return await asyncio.to_thread(_imap_sincrono, dias, maximo)
    raise MailError(f"MAIL_BACKEND desconocido: {cfg.mail_backend}")


async def comprobar() -> str:
    """Para /estado: a que cuenta esta conectado."""
    if cfg.mail_backend == "none":
        return "desactivado"
    if cfg.mail_backend == "imap":
        return cfg.imap_user
    from googleapiclient.discovery import build

    def _perfil():
        servicio = build("gmail", "v1", credentials=_credenciales(), cache_discovery=False)
        return servicio.users().getProfile(userId="me").execute().get("emailAddress", "?")

    return await asyncio.to_thread(_perfil)
