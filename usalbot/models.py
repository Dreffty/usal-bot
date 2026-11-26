"""Los datos que van y vienen: tareas de Studium y correos."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import cfg


def umbral_clave() -> int:
    """Puntos a partir de los cuales un correo es importante (SCORE_CLAVE)."""
    return cfg.score_clave


# Alias historico: antes estaba repetido en tres sitios con un 7 fijo.
UMBRAL_CLAVE = cfg.score_clave


@dataclass
class Task:
    id: str
    kind: str
    title: str
    course: str = ""
    course_id: int = 0
    url: str = ""
    due: int | None = None
    done: bool = False
    overdue: bool = False
    # Desaparecio de Studium sin /hecho: entregada o el profe la quito.
    desaparecida: bool = False
    # Avisos pospuestos hasta este epoch (ver /aplazar). None = sin aplazar.
    aplazada_hasta: int | None = None

    @property
    def due_dt(self) -> datetime | None:
        return datetime.fromtimestamp(self.due, cfg.tz) if self.due else None

    @property
    def horas_restantes(self) -> float | None:
        if not self.due:
            return None
        ahora = datetime.now(cfg.tz).timestamp()
        return (self.due - ahora) / 3600


@dataclass
class Mail:
    id: str
    subject: str = ""
    sender: str = ""
    sender_email: str = ""
    snippet: str = ""
    ts: int = 0
    url: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    unread: bool = False
    adjuntos: tuple[str, ...] = ()
    resumen: str = ""

    @property
    def dt(self) -> datetime:
        """Fecha del correo. Existe para que el formato pueda usar m.dt."""
        if self.ts:
            return datetime.fromtimestamp(self.ts, cfg.tz)
        return datetime.now(cfg.tz)

    @property
    def clave(self) -> bool:
        return self.score >= cfg.score_clave


@dataclass
class Course:
    id: int
    name: str
    shortname: str = ""
    url: str = ""
