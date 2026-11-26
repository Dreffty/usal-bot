"""Cliente del Moodle de la USAL (Studium) por su API oficial.

Studium tiene activado el servicio 'moodle_mobile_app', el mismo que usa la
app movil de Moodle. Con un token se puede preguntar directamente por los
cursos y por las entregas pendientes, sin abrir el navegador ni rascar HTML.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..config import cfg
from ..models import Course, Task

log = logging.getLogger("usalbot.studium")

# Eventos que son cosa del profesor, no del alumno.
EVENTOS_IGNORADOS = {"gradingdue", "expectcompletionon"}

# Como llamamos a cada tipo de actividad al escribirla.
NOMBRES = {
    "assign": "Entrega",
    "quiz": "Cuestionario",
    "forum": "Foro",
    "feedback": "Encuesta",
    "workshop": "Taller",
    "lesson": "Leccion",
    "choice": "Eleccion",
    "data": "Base de datos",
    "scorm": "SCORM",
    "attendance": "Asistencia",
}


async def _postear(cli: httpx.AsyncClient, url: str,
                   datos: dict[str, str] | None = None,
                   cuerpo_json: object = None) -> httpx.Response:
    """POST que sobrevive a las redirecciones de Studium.

    studium.usal.es responde 301 hacia el dominio del curso actual
    (studium26.usal.es, y el ano que viene sera otro). Ante un 301 los clientes
    HTTP reenvian como GET y se dejan el cuerpo por el camino, asi que el
    servidor recibia la peticion sin usuario ni contrasena. Aqui se repite el
    POST contra el destino, que es lo que queriamos decir.
    """
    for _ in range(4):
        r = (await cli.post(url, json=cuerpo_json) if cuerpo_json is not None
             else await cli.post(url, data=datos))
        if r.status_code in (301, 302, 303, 307, 308) and "location" in r.headers:
            url = str(httpx.URL(url).join(r.headers["location"]))
            continue
        return r
    raise StudiumError("demasiadas redirecciones al hablar con Studium")


class StudiumError(RuntimeError):
    """Algo ha dicho que no al otro lado: token caducado, funcion no permitida..."""

    def __init__(self, mensaje: str, codigo: str = ""):
        super().__init__(mensaje)
        self.codigo = codigo

    @property
    def token_caducado(self) -> bool:
        return self.codigo in {"invalidtoken", "tokennotfound", "sitemaintenance"}

    @property
    def funcion_no_permitida(self) -> bool:
        """El servicio movil no expone todas las funciones de Moodle.

        Cuando una no esta en su lista, Moodle responde 'accessexception'. No
        es que la clave haya caducado -decirlo asi manda a rehacer la clave
        para nada-, es que esa funcion concreta no esta disponible aqui.
        """
        return self.codigo in {"accessexception", "invalidparameter",
                               "webservice_access_exception"}


def _aplanar(nombre: str, valor: Any, destino: dict[str, str]) -> None:
    """Moodle quiere las listas como courseids[0], courseids[1]..."""
    if isinstance(valor, (list, tuple)):
        for i, v in enumerate(valor):
            _aplanar(f"{nombre}[{i}]", v, destino)
    elif isinstance(valor, dict):
        for k, v in valor.items():
            _aplanar(f"{nombre}[{k}]", v, destino)
    elif isinstance(valor, bool):
        destino[nombre] = "1" if valor else "0"
    else:
        destino[nombre] = str(valor)


class Studium:
    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = (url or cfg.studium_url).rstrip("/")
        self.token = token or cfg.studium_token
        self._userid: int | None = None

    # ------------------------------------------------------------ bajo nivel
    async def llamar(self, funcion: str, **params: Any) -> Any:
        datos: dict[str, str] = {
            "wstoken": self.token,
            "wsfunction": funcion,
            "moodlewsrestformat": "json",
        }
        for k, v in params.items():
            _aplanar(k, v, datos)

        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as cli:
            r = await _postear(cli, f"{self.url}/webservice/rest/server.php", datos)
            r.raise_for_status()
            try:
                resp = r.json()
            except ValueError:
                raise StudiumError(f"Respuesta ilegible de Studium en {funcion}")

        if isinstance(resp, dict) and resp.get("exception"):
            raise StudiumError(resp.get("message", "error de Studium"), resp.get("errorcode", ""))
        return resp

    @staticmethod
    async def config_publica(url: str) -> dict:
        """Como se entra en este Moodle, sin necesidad de estar dentro.

        Interesa 'typeoflogin': 1 es usuario y contrasena de toda la vida, y
        cualquier otra cosa significa que la contrasena la valida otro sitio
        (en la USAL, identidad.usal.es por SAML) y que token.php no sirve.
        """
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as cli:
            r = await _postear(
                cli,
                f"{url.rstrip('/')}/lib/ajax/service-nologin.php"
                "?info=tool_mobile_get_public_config",
                cuerpo_json=[{"index": 0,
                              "methodname": "tool_mobile_get_public_config",
                              "args": {}}],
            )
            r.raise_for_status()
            try:
                datos = r.json()
            except ValueError:
                raise StudiumError("el sitio no ha contestado con datos legibles")
        if not isinstance(datos, list) or not datos:
            raise StudiumError("no he podido leer la configuracion del sitio")
        if datos[0].get("error"):
            raise StudiumError(str(datos[0].get("exception") or datos[0]["error"]))
        return datos[0].get("data") or {}

    @staticmethod
    async def pedir_token(url: str, usuario: str, contrasena: str) -> str:
        """Cambia usuario+contrasena por un token permanente. Solo en el asistente."""
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as cli:
            r = await _postear(cli, f"{url.rstrip('/')}/login/token.php",
                               {"username": usuario, "password": contrasena,
                                "service": "moodle_mobile_app",
                                "moodlewsrestformat": "json"})
            r.raise_for_status()
            try:
                datos = r.json()
            except ValueError:
                raise StudiumError("Studium no ha devuelto un token legible")
        if "token" not in datos:
            raise StudiumError(datos.get("error", "no se pudo obtener el token"),
                               datos.get("errorcode", ""))
        return datos["token"]

    # ------------------------------------------------------------ alto nivel
    async def info(self) -> dict:
        datos = await self.llamar("core_webservice_get_site_info")
        self._userid = datos.get("userid")
        return datos

    async def userid(self) -> int:
        if self._userid is None:
            await self.info()
        return int(self._userid or 0)

    async def cursos(self) -> list[Course]:
        uid = await self.userid()
        crudos = await self.llamar("core_enrol_get_users_courses", userid=uid)
        salida = []
        for c in crudos:
            if c.get("hidden"):
                continue
            salida.append(Course(
                id=int(c["id"]),
                name=c.get("fullname", "").strip(),
                shortname=c.get("shortname", "").strip(),
                url=f"{self.url}/course/view.php?id={c['id']}",
            ))
        return salida

    # Moodle rechaza la peticion si se le piden mas: "Limit must be between
    # 1 and 50 (inclusive)".
    TOPE_EVENTOS = 50

    async def pendientes(self, dias_atras: int = 21, dias_adelante: int = 365,
                         limite: int = TOPE_EVENTOS) -> list[Task]:
        """Lo que Moodle considera que TE toca hacer.

        Es la misma lista que ves en 'Linea de tiempo' de la pagina principal:
        en cuanto entregas algo, deja de aparecer. Por eso sirve como fuente
        de verdad de lo que queda pendiente.
        """
        ahora = int(time.time())
        datos = await self.llamar(
            "core_calendar_get_action_events_by_timesort",
            timesortfrom=ahora - dias_atras * 86400,
            timesortto=ahora + dias_adelante * 86400,
            limitnum=max(1, min(limite, self.TOPE_EVENTOS)),
            limittononsuspendedevents=True,
        )
        tareas: list[Task] = []
        for ev in datos.get("events", []):
            tarea = self._leer_evento(ev)
            if tarea is not None:
                tareas.append(tarea)
        return tareas

    def _leer_evento(self, ev: dict) -> Task | None:
        """Convierte un evento del calendario en una tarea, o lo descarta.

        Un solo evento con un campo raro no puede tumbar la lista entera: si
        Moodle cambia algo o llega algo inesperado, se pierde ESE evento y el
        resto sigue llegando.
        """
        try:
            if ev.get("eventtype") in EVENTOS_IGNORADOS:
                return None
            accion = ev.get("action") or {}
            # actionable=False significa "ya no puedes hacer nada": entregado o cerrado.
            if accion and accion.get("actionable") is False and not ev.get("overdue"):
                return None
            if ev.get("id") is None:
                return None

            curso = ev.get("course") or {}
            titulo = (ev.get("activityname") or ev.get("name") or "").strip()
            if not titulo:
                return None

            try:
                vence = int(ev.get("timesort") or ev.get("timestart") or 0) or None
            except (TypeError, ValueError):
                vence = None

            return Task(
                id=f"studium:ev:{ev['id']}",
                kind=ev.get("modulename") or "event",
                title=titulo,
                course=(curso.get("fullname") or curso.get("shortname") or "").strip(),
                course_id=int(curso.get("id") or 0),
                url=accion.get("url") or ev.get("url") or "",
                due=vence,
                done=False,
                overdue=bool(ev.get("overdue")),
            )
        except Exception:
            log.warning("evento de Studium ilegible, lo salto: %r", ev)
            return None

    async def entregas_del_curso(self, course_id: int) -> list[Task]:
        """Todas las entregas de una asignatura, tambien las ya pasadas."""
        datos = await self.llamar("mod_assign_get_assignments", courseids=[course_id])
        tareas: list[Task] = []
        for curso in datos.get("courses", []):
            nombre = curso.get("fullname", "")
            for a in curso.get("assignments", []):
                vence = int(a.get("duedate") or 0) or None
                tareas.append(Task(
                    id=f"studium:assign:{a['id']}",
                    kind="assign",
                    title=a.get("name", "").strip(),
                    course=nombre,
                    course_id=int(curso.get("id") or course_id),
                    url=f"{self.url}/mod/assign/view.php?id={a.get('cmid')}",
                    due=vence,
                    done=bool(vence and vence < time.time()),
                ))
        return tareas

    async def inventario(self, course_ids: list[int]) -> list[Task]:
        """Todas las entregas y cuestionarios de tus asignaturas.

        La linea de tiempo solo trae lo que tiene fecha y sigue pendiente; esto
        trae el resto, incluidas las tareas que el profesor cuelga sin fecha.

        Son dos llamadas para TODAS las asignaturas, no una por asignatura:
        Moodle acepta la lista entera de cursos de una vez.
        """
        if not course_ids:
            return []
        nombres = {c.id: c.name for c in await self.cursos()}
        salida: list[Task] = []

        try:
            datos = await self.llamar("mod_assign_get_assignments", courseids=course_ids)
            for curso in datos.get("courses", []):
                cid = int(curso.get("id") or 0)
                for a in curso.get("assignments", []):
                    vence = int(a.get("duedate") or 0) or None
                    salida.append(Task(
                        id=f"studium:assign:{a['id']}",
                        kind="assign",
                        title=a.get("name", "").strip(),
                        course=curso.get("fullname") or nombres.get(cid, ""),
                        course_id=cid,
                        url=f"{self.url}/mod/assign/view.php?id={a.get('cmid')}",
                        due=vence,
                    ))
        except StudiumError as e:
            if not e.funcion_no_permitida:
                raise

        try:
            datos = await self.llamar("mod_quiz_get_quizzes_by_courses",
                                      courseids=course_ids)
            for q in datos.get("quizzes", []):
                cid = int(q.get("course") or 0)
                cierra = int(q.get("timeclose") or 0) or None
                salida.append(Task(
                    id=f"studium:quiz:{q['id']}",
                    kind="quiz",
                    title=q.get("name", "").strip(),
                    course=nombres.get(cid, ""),
                    course_id=cid,
                    url=f"{self.url}/mod/quiz/view.php?id={q.get('coursemodule')}",
                    due=cierra,
                ))
        except StudiumError as e:
            if not e.funcion_no_permitida:
                raise

        return salida

    async def funciones_disponibles(self) -> list[str]:
        """Que sabe hacer el servicio movil de ESTE Studium."""
        datos = await self.info()
        return sorted(f.get("name", "") for f in datos.get("functions", []))

    async def comprobar(self) -> str:
        """Para /estado: devuelve el nombre del usuario si el token vale."""
        datos = await self.info()
        return datos.get("fullname", "?")


def etiqueta(kind: str) -> str:
    return NOMBRES.get(kind, kind.capitalize())
