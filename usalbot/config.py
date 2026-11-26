"""Configuracion: se lee del fichero .env una sola vez y se comparte."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()}


def _si_o_no(raw: str) -> bool:
    return raw.strip().lower() not in ("no", "0", "false", "")


def _entero(raw: str | None, defecto: int, minimo: int, maximo: int) -> int:
    """Un numero del .env que no peta aunque venga mal escrito."""
    try:
        valor = int((raw or "").strip() or defecto)
    except (TypeError, ValueError):
        return defecto
    return min(max(valor, minimo), maximo)


def _zona(raw: str | None) -> ZoneInfo:
    nombre = (raw or "").strip() or "Europe/Madrid"
    try:
        return ZoneInfo(nombre)
    except Exception:
        return ZoneInfo("Europe/Madrid")


def _hhmm(raw: str | None) -> time | None:
    """Una hora del .env que no peta aunque venga mal escrita."""
    texto = (raw or "").strip()
    if not texto:
        return None
    try:
        hh, _, mm = texto.partition(":")
        hora, minuto = int(hh), int(mm or 0)
    except (TypeError, ValueError):
        return None
    if not (0 <= hora < 24 and 0 <= minuto < 60):
        return None
    return time(hour=hora, minute=minuto)


@dataclass(frozen=True)
class Config:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    allowed_ids: set[int] = field(default_factory=lambda: _ids(os.getenv("TELEGRAM_ALLOWED_IDS", "")))

    studium_url: str = os.getenv("STUDIUM_URL", "https://studium.usal.es").rstrip("/")
    studium_token: str = os.getenv("STUDIUM_TOKEN", "")
    # Alternativa cuando la universidad no deja crear claves de API.
    studium_ical_url: str = os.getenv("STUDIUM_ICAL_URL", "").strip()

    mail_backend: str = os.getenv("MAIL_BACKEND", "gmail").lower()
    imap_host: str = os.getenv("IMAP_HOST", "imap.gmail.com")
    imap_port: int = _entero(os.getenv("IMAP_PORT"), 993, 1, 65535)
    imap_user: str = os.getenv("IMAP_USER", "")
    imap_password: str = os.getenv("IMAP_PASSWORD", "")

    llm_base_url: str = os.getenv("LLM_BASE_URL", "").rstrip("/")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    # Mandar el CUERPO de los correos importantes al modelo para resumirlos.
    # Con esto en "no" el resumen se saca del propio texto y nada sale de aqui.
    resumir_correos: bool = _si_o_no(os.getenv("RESUMIR_CORREOS", "si"))

    sync_minutes: int = _entero(os.getenv("SYNC_MINUTES"), 10, 5, 120)
    # Puntos a partir de los cuales un correo cuenta como importante.
    score_clave: int = _entero(os.getenv("SCORE_CLAVE"), 7, 0, 100)
    daily_digest_at: time | None = field(default_factory=lambda: _hhmm(os.getenv("DAILY_DIGEST_AT", "08:00")))
    tz: ZoneInfo = field(default_factory=lambda: _zona(os.getenv("TIMEZONE")))
    db_path: Path = field(default_factory=lambda: ROOT / os.getenv("DB_PATH", "usalbot.db"))

    gmail_client_secret: Path = ROOT / "client_secret.json"
    gmail_token_file: Path = ROOT / "token_gmail.json"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model)

    def missing(self) -> list[str]:
        """Lo que falta para poder arrancar."""
        faltan = []
        if not self.telegram_token:
            faltan.append("TELEGRAM_TOKEN")
        if not self.allowed_ids:
            faltan.append("TELEGRAM_ALLOWED_IDS")
        if not self.studium_token and not self.studium_ical_url:
            faltan.append("STUDIUM_TOKEN o STUDIUM_ICAL_URL "
                          "(ejecuta: python setup_wizard.py studium)")
        return faltan

    @property
    def usa_calendario(self) -> bool:
        """Sin clave de API se tira del calendario exportado."""
        return not self.studium_token and bool(self.studium_ical_url)


cfg = Config()
