#!/usr/bin/env python3
"""Asistente de configuracion. Se ejecuta a mano, una vez por cuenta.

  python setup_wizard.py init      crea el fichero .env a partir del ejemplo
  python setup_wizard.py telegram  averigua tu id escribiendole al bot
  python setup_wizard.py studium   conecta con Studium (detecta si hay SSO)
  python setup_wizard.py studium --token <clave>    si ya tienes la clave
  python setup_wizard.py studium --sesion <valor>  clave de API via tu sesion
  python setup_wizard.py studium --ical <url>      via calendario exportado
  python setup_wizard.py gmail     autoriza la lectura del correo
  python setup_wizard.py check     comprueba que todo responde

Tu contrasena de la USAL no se guarda en ningun sitio ni se le manda a nadie.
En la USAL ni siquiera hace falta: se entra por idUSAL (SSO), asi que la clave
se crea desde la web de Studium y aqui solo se pega.
"""

from __future__ import annotations

import asyncio
import getpass
import re
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ENV = RAIZ / ".env"
EJEMPLO = RAIZ / ".env.example"


def escribir_env(clave: str, valor: str) -> None:
    texto = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    patron = re.compile(rf"^{re.escape(clave)}=.*$", re.M)
    linea = f"{clave}={valor}"
    texto = patron.sub(linea, texto) if patron.search(texto) else texto.rstrip("\n") + f"\n{linea}\n"
    ENV.write_text(texto, encoding="utf-8")
    print(f"  ✓ {clave} guardado en .env")


def cmd_init() -> int:
    if ENV.exists():
        print("  · .env ya existe, no lo toco")
    else:
        shutil.copy(EJEMPLO, ENV)
        print("  ✓ .env creado a partir de .env.example")
    print("\nAhora abre .env y pon tu TELEGRAM_TOKEN (te lo da @BotFather).")
    print("Luego:  python setup_wizard.py telegram")
    return 0


def leer_env(clave: str) -> str:
    if not ENV.exists():
        return ""
    for linea in ENV.read_text(encoding="utf-8").splitlines():
        if linea.startswith(f"{clave}="):
            return linea.split("=", 1)[1].strip()
    return ""


def cmd_telegram() -> int:
    """Espera a que escribas al bot y se queda con tu id.

    Asi no hay que ir a buscarlo a ningun otro sitio: el propio mensaje que le
    mandas ya lleva dentro quien eres.
    """
    import httpx

    token = leer_env("TELEGRAM_TOKEN")
    if not token or token.startswith("123456:"):
        print("Primero pon tu TELEGRAM_TOKEN en el .env (te lo da @BotFather).")
        return 1

    api = f"https://api.telegram.org/bot{token}"
    try:
        yo = httpx.get(f"{api}/getMe", timeout=15).json()
    except Exception as e:
        print(f"No he podido hablar con Telegram: {e}")
        return 1
    if not yo.get("ok"):
        print(f"Ese token no vale: {yo.get('description')}")
        return 1

    nombre = yo["result"]["username"]
    print(f"Tu bot es @{nombre}\n")
    print(f"  👉 Abre https://t.me/{nombre} y mandale cualquier mensaje (un 'hola' vale).")
    print("     Esperando... (Ctrl+C para dejarlo)\n")

    # Nos saltamos lo que hubiera antes: solo cuenta lo que escribas ahora.
    try:
        previos = httpx.get(f"{api}/getUpdates", timeout=15).json().get("result", [])
        desde = previos[-1]["update_id"] + 1 if previos else 0

        for _ in range(30):  # unos 5 minutos
            r = httpx.get(f"{api}/getUpdates",
                          params={"offset": desde, "timeout": 10}, timeout=25).json()
            for upd in r.get("result", []):
                msg = upd.get("message") or upd.get("edited_message") or {}
                quien = msg.get("from") or {}
                if quien.get("id"):
                    uid = quien["id"]
                    print(f"  ✓ Eres {quien.get('first_name','')} (id {uid})")
                    escribir_env("TELEGRAM_ALLOWED_IDS", str(uid))
                    print("\nSiguiente:  python setup_wizard.py studium")
                    return 0
    except KeyboardInterrupt:
        print("\nDejado a medias.")
        return 1
    except Exception as e:
        print(f"Se ha torcido: {e}")
        return 1

    print("No ha llegado ningun mensaje. Vuelve a intentarlo.")
    return 1


def _guardar_y_probar(token: str) -> int:
    """Comprueba que la clave sirve y la guarda.

    La comprobacion es la llamada mas simple que hay. Antes se probaba tambien
    a traer cursos y tareas, y si cualquiera de esas fallaba se daba la clave
    por mala y se tiraba: una clave perfectamente buena se perdia por un
    parametro mal puesto en otra consulta.
    """
    from usalbot.config import cfg
    from usalbot.sources.studium import Studium, StudiumError

    cliente = Studium(cfg.studium_url, token)

    try:
        datos = asyncio.run(cliente.info())
    except StudiumError as e:
        if e.token_caducado:
            print(f"\n✗ Esa clave no vale: {e}")
        else:
            print(f"\n✗ Studium ha respondido con un error ({e.codigo}): {e}")
        return 1
    except Exception as e:
        print(f"\n✗ No he podido comprobarla: {e}")
        return 1

    # La clave vale: se guarda ya, antes de nada mas.
    print(f"\n✓ Conectado como {datos.get('fullname')} ({datos.get('username')})")
    escribir_env("STUDIUM_TOKEN", token)

    # Lo de abajo es solo para enseñar que se ve. Si algo falla, la clave
    # sigue siendo buena y ya esta guardada.
    try:
        cursos = asyncio.run(cliente.cursos())
        tareas = asyncio.run(cliente.pendientes())
        print(f"  {len(cursos)} asignaturas · {len(tareas)} cosas pendientes")
        from usalbot.format import cuanto_falta
        for t in tareas[:6]:
            print(f"    · {t.title[:45]:<45} {cuanto_falta(t)}")
        if not cursos:
            print("\n  ⚠ Cero asignaturas. Comprueba que es el Studium de ESTE curso:")
            print(f"     {cfg.studium_url} deberia llevarte al que tiene tus")
            print("     asignaturas. Si ahi tampoco las ves, aun no te han dado de alta.")
    except StudiumError as e:
        print(f"  (no he podido listar tus asignaturas: {e.codigo or e})")
        print("   La clave esta guardada igualmente.")
    except Exception as e:
        print(f"  (no he podido listar tus asignaturas: {e})")
        print("   La clave esta guardada igualmente.")

    print("\nSiguiente:  python setup_wizard.py gmail")
    print("            (o pon MAIL_BACKEND=none en el .env si pasas del correo)")
    return 0


def _por_sesion(url: str, valor: str = "") -> int:
    """La via que de verdad funciona en Studium para sacar la clave de API."""
    import random

    from usalbot import sso_token

    if not valor:
        print("\nVia de la sesion (esta si da la clave de API completa):\n")
        print("  1. Abre Studium en el navegador, con tu sesion iniciada.")
        print("  2. Pulsa F12 (o clic derecho -> Inspeccionar).")
        print("  3. Pestana «Application» (Chrome) o «Almacenamiento» (Firefox).")
        print("  4. A la izquierda: Cookies -> https://studium26.usal.es")
        print("  5. Busca la fila «MoodleSession» y copia su columna Value.\n")
        print("  Ese valor identifica tu sesion. Se usa UNA vez, aqui en tu")
        print("  ordenador, y no se guarda: lo que se guarda es la clave de API,")
        print("  que es permanente. Luego puedes cerrar sesion y anularlo.\n")
        try:
            valor = input("Pegalo aqui: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nCuando lo tengas: python setup_wizard.py studium --sesion <valor>")
            return 1
    if not valor:
        print("Sin eso no puedo. Queda la via del calendario:")
        print("  python setup_wizard.py studium --ical <direccion>")
        return 1

    passport = round(random.uniform(0, 1000), 6)
    r = sso_token.pedir_con_sesion(url, valor, passport)
    if r.error and not r.token:
        print(f"\n✗ {r.error}")
        if r.crudo:
            print(f"  Respuesta: {r.crudo[:140]}")
        print("\n  Si no hay manera, la via del calendario siempre funciona:")
        print("    python setup_wizard.py studium --ical <direccion>")
        return 1
    if r.error:
        print(f"⚠️  {r.error}")
    print("\n✓ Studium ha soltado la clave de API.")
    return _guardar_y_probar(r.token)


def _por_sesion_del_navegador(url: str) -> int:
    """Pedirle la clave a Moodle aprovechando que ya estas dentro.

    Es la puerta que usa la app movil: si tienes la sesion abierta, Moodle
    suelta la clave sin preguntar nada mas. La recoge un servidor diminuto que
    se levanta aqui y se apaga en cuanto llega.
    """
    import random

    from usalbot import sso_token

    passport = round(random.uniform(0, 1000), 6)
    puerto = sso_token.puerto_libre()
    lanzar = sso_token.url_de_lanzamiento(url, puerto, passport)

    print("\nVia de la sesion del navegador (la que usa la app movil):\n")
    print("  1. Asegurate de tener Studium abierto y con la sesion iniciada.")
    print("  2. Abre esta direccion en ese mismo navegador:\n")
    print(f"     {lanzar}\n")
    print("  3. Si te pregunta, acepta. La pagina dira 'Clave recibida'.\n")
    print("  Esperando (5 minutos)... Ctrl+C para dejarlo.\n")

    try:
        r = sso_token.esperar_clave(url, puerto, passport)
    except KeyboardInterrupt:
        print("Dejado a medias.")
        return 1

    if r.error and not r.token:
        print(f"✗ {r.error}")
        if r.crudo:
            print(f"  Lo que llego: {r.crudo[:160]}")
        print("\n  Si Studium no ha soltado la clave por aqui, queda la via del")
        print("  calendario:  python setup_wizard.py studium --ical <direccion>")
        return 1
    if r.error:
        print(f"⚠️  {r.error}")
        print("  Aun asi ha llegado una clave; la pruebo por si acaso.")

    return _guardar_y_probar(r.token)


def _por_calendario(url: str) -> int:
    """Cuando la universidad tampoco deja crear claves de seguridad.

    Moodle deja exportar tu calendario con una direccion permanente. Ahi estan
    las mismas fechas de entrega, y no hace falta ningun permiso especial.
    """
    from usalbot.sources import studium_ical

    print("\nVia del calendario (no necesita permisos):\n")
    print(f"  1. Abre:  {url}/calendar/view.php")
    print("  2. Baja del todo y pulsa «Exportar calendario».")
    print("  3. Marca «Todos los eventos» y el periodo mas largo que te deje")
    print("     («Este ano» o «Ano proximo»; NO «proximos 60 dias»).")
    print("  4. Pulsa «Obtener URL del calendario» y copia la direccion larga")
    print("     que aparece (empieza por http y lleva un authtoken dentro).\n")

    try:
        direccion = input("Pega aqui la direccion (o Enter para dejarlo): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n\nCuando la tengas:  python setup_wizard.py studium --ical <direccion>")
        return 1
    if not direccion:
        print("Dejado a medias. Cuando la tengas:")
        print("  python setup_wizard.py studium --ical <direccion>")
        return 1
    return _guardar_calendario(direccion)


def _guardar_calendario(direccion: str) -> int:
    from usalbot.sources import studium_ical

    try:
        tareas = asyncio.run(studium_ical.pendientes(direccion))
    except studium_ical.IcalError as e:
        print(f"\n✗ {e}")
        return 1
    except Exception as e:
        print(f"\n✗ No he podido descargarlo: {e}")
        return 1

    cursos = studium_ical.cursos_de(tareas)
    print(f"\n✓ Calendario leido: {len(tareas)} eventos en {len(cursos)} asignaturas")
    for t in tareas[:5]:
        from usalbot.format import cuanto_falta
        print(f"    · {t.title[:42]:<42} {cuanto_falta(t)}")
    if not tareas:
        print("  (no hay eventos: puede que hayas elegido un periodo muy corto)")

    escribir_env("STUDIUM_ICAL_URL", direccion)
    print("\n  Ojo: el calendario no sabe lo que ya has entregado, asi que una")
    print("  tarea hecha sigue saliendo hasta su fecha. En el bot: /hecho <nombre>.")
    print("\nSiguiente:  python setup_wizard.py gmail")
    return 0


def _por_sso(url: str, proveedor: str) -> int:
    """Cuando la contrasena la valida otro sitio y Moodle no la conoce.

    Es el caso de la USAL: se entra con idUSAL contra identidad.usal.es, asi
    que no hay usuario y contrasena que darle a Moodle. La salida es crear la
    clave desde la propia web, ya con la sesion iniciada, y pegarla aqui.
    """
    print(f"\nEste Studium entra por «{proveedor}», no con usuario y contrasena.")
    print("Tu contrasena la comprueba el servidor de identidad de la USAL, no")
    print("Moodle, asi que hay que crear la clave desde la web. Es un minuto:\n")
    print(f"  1. Abre:  {url}/user/managetoken.php")
    print("     (si te pide entrar, entra con idUSAL como siempre)")
    print("  2. Busca la fila del servicio movil de Moodle")
    print("     — suele llamarse 'Moodle mobile web service' o 'Servicio movil'.")
    print("  3. Si no hay ninguna, pulsa 'Crear clave de seguridad' y elige ese servicio.")
    print("  4. Copia la clave: son 32 letras y numeros seguidos.\n")

    try:
        token = input("Pega la clave, o Enter si esa pagina no te deja: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n\nCuando la tengas:  python setup_wizard.py studium --token <clave>")
        return 1
    if not token:
        # Lo normal: los alumnos no pueden crear claves a mano. Se intenta la
        # puerta de la app movil, que da la MISMA clave de API.
        print("\nSin problema: hay otra forma de sacar esa misma clave.")
        return _por_sesion(url)
    if len(token) < 20 or not token.isalnum():
        print("\nEso no parece una clave: son 32 caracteres, solo letras y numeros.")
        print("Asegurate de copiar la clave y no el nombre del servicio.")
        return 1
    return _guardar_y_probar(token)


def cmd_studium() -> int:
    from usalbot.config import cfg
    from usalbot.sources.studium import Studium, StudiumError

    url = cfg.studium_url

    # Si ya traes la clave, no damos mas vueltas.
    if len(sys.argv) > 2 and sys.argv[2] in ("--token", "-t"):
        token = sys.argv[3] if len(sys.argv) > 3 else input("Pega la clave: ").strip()
        return _guardar_y_probar(token)
    if len(sys.argv) > 2 and sys.argv[2] in ("--sesion", "-s"):
        return _por_sesion(cfg.studium_url, sys.argv[3] if len(sys.argv) > 3 else "")
    if len(sys.argv) > 2 and sys.argv[2] in ("--ical", "-i"):
        direccion = sys.argv[3] if len(sys.argv) > 3 else input("Pega la direccion: ").strip()
        return _guardar_calendario(direccion)

    print(f"Studium: {url}")
    try:
        config = asyncio.run(Studium.config_publica(url))
    except Exception as e:
        print(f"  (no he podido preguntar como se entra aqui: {e})")
        config = {}

    if config.get("sitename"):
        print(f"Sitio: {config['sitename']}")

    proveedores = config.get("identityproviders") or []
    tipo = config.get("typeoflogin")

    if not config:
        # Sin saber como entra este sitio, mejor preguntar que asumir.
        try:
            r = input("¿Entras en Studium pulsando un botón tipo 'idUSAL'? [S/n]: ")
        except EOFError:
            r = "s"
        if r.strip().lower() not in ("n", "no"):
            return _por_sso(url, "idUSAL")

    # typeoflogin 1 = usuario y contrasena contra el propio Moodle.
    # Cualquier otro valor, o que haya proveedores externos, significa SSO.
    if tipo not in (1, None) or proveedores:
        nombre = proveedores[0].get("name", "inicio de sesion externo") if proveedores \
            else "inicio de sesion externo"
        return _por_sso(url, nombre)

    try:
        usuario = input("Usuario (idUSAL): ").strip()
        contrasena = getpass.getpass("Contrasena (no se guarda): ")
    except (EOFError, KeyboardInterrupt):
        print("\n\nEsto hay que escribirlo en una terminal de verdad.")
        print("Abre la app Terminal y ejecuta ahi el mismo comando.")
        return 1
    try:
        token = asyncio.run(Studium.pedir_token(url, usuario, contrasena))
    except StudiumError as e:
        print(f"\n✗ Studium ha dicho que no: {e}")
        if e.codigo == "invalidlogin":
            print("\n  Si estas seguro de la contrasena, es que este Studium no")
            print("  la valida el mismo. Prueba la otra via:")
            print(f"    {url}/user/managetoken.php")
            print("    y luego:  python setup_wizard.py studium --token")
        return 1
    return _guardar_y_probar(token)


def cmd_gmail() -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow

    from usalbot.config import cfg
    from usalbot.sources.mail import SCOPES

    if not cfg.gmail_client_secret.exists():
        print("Falta client_secret.json. Se saca en 4 minutos:\n")
        print("  1. Entra en https://console.cloud.google.com/ y crea un proyecto.")
        print("  2. APIs y servicios -> Biblioteca -> activa 'Gmail API'.")
        print("  3. Pantalla de consentimiento OAuth: tipo Externo, anadete como usuario de prueba.")
        print("  4. Credenciales -> Crear credenciales -> ID de cliente de OAuth")
        print("     -> tipo 'Aplicacion de escritorio'. Descarga el JSON.")
        print(f"  5. Guardalo como: {cfg.gmail_client_secret}\n")
        print("Y vuelve a ejecutar este comando.")
        return 1

    flujo = InstalledAppFlow.from_client_secrets_file(str(cfg.gmail_client_secret), SCOPES)
    creds = flujo.run_local_server(port=0, prompt="consent",
                                   authorization_prompt_message="Abriendo el navegador para autorizar...")
    cfg.gmail_token_file.write_text(creds.to_json())
    print(f"\n✓ Autorizado. Guardado en {cfg.gmail_token_file.name}")
    print("  Permiso concedido: solo LECTURA del correo.")
    return 0


def cmd_check() -> int:
    from usalbot import db
    from usalbot.config import cfg
    from usalbot.sources import mail
    from usalbot.sources.studium import Studium

    db.init()
    fallos = 0

    faltan = cfg.missing()
    if faltan:
        print("✗ Configuracion incompleta: " + ", ".join(faltan))
        fallos += 1
    else:
        print("✓ Configuracion basica completa")

    async def _pruebas():
        nonlocal fallos
        if cfg.usa_calendario:
            try:
                from usalbot.sources import studium_ical
                tareas = await studium_ical.pendientes(cfg.studium_ical_url)
                cursos = studium_ical.cursos_de(tareas)
                print(f"✓ Studium (calendario): {len(cursos)} asignaturas · "
                      f"{len(tareas)} eventos")
                from usalbot.format import cuanto_falta
                for t in tareas[:5]:
                    print(f"    · {t.title[:45]:<45} {cuanto_falta(t)}")
            except Exception as e:
                print(f"✗ Calendario de Studium: {e}")
                fallos += 1
        elif cfg.studium_token:
            try:
                cliente = Studium()
                nombre = await cliente.comprobar()
                cursos = await cliente.cursos()
                tareas = await cliente.pendientes()
                print(f"✓ Studium: {nombre} · {len(cursos)} asignaturas · {len(tareas)} pendientes")
                try:
                    funciones = await cliente.funciones_disponibles()
                    usadas = ["core_enrol_get_users_courses",
                              "core_calendar_get_action_events_by_timesort",
                              "mod_assign_get_assignments",
                              "mod_quiz_get_quizzes_by_courses"]
                    faltan = [f for f in usadas if f not in funciones]
                    print(f"  {len(funciones)} funciones disponibles en este Studium")
                    if faltan:
                        print("  ⚠ no expone: " + ", ".join(faltan))
                        print("    (el bot funciona igual, con menos detalle)")
                except Exception:
                    pass
                for t in tareas[:5]:
                    from usalbot.format import cuanto_falta
                    print(f"    · {t.title[:45]:<45} {cuanto_falta(t)}")
            except Exception as e:
                print(f"✗ Studium: {e}")
                fallos += 1
        if cfg.mail_backend != "none":
            try:
                cuenta = await mail.comprobar()
                correos = await mail.recientes(dias=3, maximo=10)
                print(f"✓ Correo: {cuenta} · {len(correos)} correos en 3 dias")
            except Exception as e:
                print(f"✗ Correo: {e}")
                fallos += 1

    asyncio.run(_pruebas())
    print("\nTodo listo." if not fallos else f"\n{fallos} cosa(s) por arreglar.")
    return 1 if fallos else 0


COMANDOS = {"init": cmd_init, "telegram": cmd_telegram, "studium": cmd_studium,
            "gmail": cmd_gmail, "check": cmd_check}

if __name__ == "__main__":
    orden = sys.argv[1] if len(sys.argv) > 1 else ""
    if orden not in COMANDOS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMANDOS[orden]())
