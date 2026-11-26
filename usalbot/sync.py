"""Traer datos de Studium y del correo, guardarlos y decir que hay de nuevo."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from . import db
from .models import Mail, Task
from .config import cfg
from . import resumen as resumir_mod
from .sources import mail, studium_ical
from .sources.studium import Studium, StudiumError
from .triage import puntuar_todos

log = logging.getLogger("usalbot.sync")


@dataclass
class Resultado:
    tareas_nuevas: list[Task] = field(default_factory=list)
    # (tarea, fecha que tenia antes) para cada entrega que han movido.
    fechas_movidas: list[tuple[Task, int | None]] = field(default_factory=list)
    correos_nuevos: list[Mail] = field(default_factory=list)
    total_pendientes: int = 0
    entregadas: int = 0
    errores: list[str] = field(default_factory=list)

    @property
    def hubo_error(self) -> bool:
        return bool(self.errores)


# Si ya hay una sincronizacion en marcha, el que llegue despues se engancha a
# ella en vez de empezar otra. Sin esto, tres mensajes seguidos nada mas
# arrancar lanzaban tres sincronizaciones enteras a la vez: nueve llamadas a
# Studium y tres lecturas completas del buzon para traer lo mismo.
_en_curso: asyncio.Task | None = None


async def sincronizar(con_correo: bool = True, dias_correo: int = 14) -> Resultado:
    global _en_curso
    if _en_curso is not None and not _en_curso.done():
        return await asyncio.shield(_en_curso)

    _en_curso = asyncio.create_task(_sincronizar(con_correo, dias_correo))
    try:
        return await _en_curso
    finally:
        if _en_curso is not None and _en_curso.done():
            _en_curso = None


async def _sincronizar(con_correo: bool, dias_correo: int) -> Resultado:
    res = Resultado()

    # --- Studium -------------------------------------------------------
    try:
        if cfg.usa_calendario:
            # El calendario no distingue entregado de quitado, asi que lo que
            # deja de salir va a /entregadas en vez de borrarse (igual que en
            # modo API). Con la lista vacia no se marca nada: seria esconderlo
            # todo por un fallo de red.
            tareas = await studium_ical.pendientes(cfg.studium_ical_url)
            db.guardar_cursos(studium_ical.cursos_de(tareas))
            if tareas:
                res.entregadas = db.marcar_desaparecidas_como_hechas({t.id for t in tareas})
        else:
            studium = Studium()
            cursos = await studium.cursos()
            db.guardar_cursos(cursos)
            tareas = await studium.pendientes()

            # La linea de tiempo solo trae lo que tiene fecha. Lo que el
            # profesor cuelga SIN fecha no sale por ningun lado, asi que se
            # completa con el inventario de la asignatura.
            #
            # Solo se anaden las que no tienen fecha: una entrega con fecha
            # futura que no esta en la linea de tiempo es que ya la hiciste
            # (Moodle la quita al entregar), y meterla seria un falso pendiente.
            try:
                conocidas = {t.title.strip().lower() for t in tareas}
                for extra in await studium.inventario([c.id for c in cursos]):
                    if extra.due is None and extra.title.strip().lower() not in conocidas:
                        tareas.append(extra)
            except StudiumError:
                pass  # sin inventario seguimos teniendo la linea de tiempo

            # Se cuenta al final, con la lista ya completa. Hacerlo antes de
            # anadir las tareas sin fecha las contaba a todas como entregadas
            # en CADA pasada, y /sync decia "3 han dejado de estar pendientes"
            # una y otra vez sin que nadie hubiera entregado nada.
            res.entregadas = db.marcar_desaparecidas_como_hechas({t.id for t in tareas})

        res.tareas_nuevas, res.fechas_movidas = db.guardar_tareas(tareas)
        res.total_pendientes = len(tareas)
        db.put("ultimo_sync_studium", str(int(time.time())))
    except studium_ical.IcalError as e:
        res.errores.append(f"Calendario de Studium: {e}")
    except StudiumError as e:
        if e.token_caducado:
            aviso = ("La clave de Studium ya no vale. Vuelve a ejecutar: "
                     "python setup_wizard.py studium")
        elif e.funcion_no_permitida:
            # Que Studium no exponga una funcion no tiene nada que ver con la
            # clave: mandar a rehacerla seria hacer perder el tiempo.
            aviso = (f"Studium no permite una de las consultas ({e.codigo}). "
                     "No es culpa de tu clave; esa función no está disponible aquí.")
        else:
            aviso = f"Studium: {e}"
        res.errores.append(aviso)
        log.warning("fallo en studium: %s", e)
    except Exception as e:  # red caida, DNS, etc.
        res.errores.append(f"No he podido hablar con Studium ({type(e).__name__})")
        log.exception("fallo inesperado en studium")

    # --- Correo --------------------------------------------------------
    if con_correo:
        try:
            correos = puntuar_todos(await mail.recientes(dias=dias_correo))
            res.correos_nuevos = db.guardar_correos(correos)

            # Solo se resumen los que ya han pasado el filtro de importantes, y
            # unos pocos por pasada: cada uno cuesta una llamada al modelo.
            #
            # Si el modelo no contesta al primero, no se le pregunta por los
            # demas en esta pasada: el endpoint se cae a ratos y esperar ocho
            # segundos por correo dejaria la sincronizacion parada un minuto.
            modelo_responde = True
            for pendiente in db.correos_clave_sin_resumen():
                texto = ""
                if modelo_responde:
                    texto = await resumir_mod.con_modelo(pendiente)
                    modelo_responde = bool(texto)
                texto = texto or resumir_mod.extracto(pendiente)
                if texto:
                    db.guardar_resumen(pendiente.id, texto)

            db.put("ultimo_sync_correo", str(int(time.time())))
        except mail.MailError as e:
            res.errores.append(f"Correo: {e}")
        except Exception as e:
            res.errores.append(f"No he podido leer el correo ({type(e).__name__})")
            log.exception("fallo inesperado en correo")

    return res
