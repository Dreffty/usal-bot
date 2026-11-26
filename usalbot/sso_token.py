"""Conseguir la clave de API aprovechando la sesion del navegador.

Moodle tiene una puerta pensada para su app movil: le pides que te lance la
app y, si ya estas dentro, te devuelve la clave sin preguntarte nada. La app
recibe esa clave por una direccion del tipo "miapp://token=...".

Aqui se hace lo mismo, pero el sitio al que Moodle devuelve la clave es un
servidor diminuto que corre en este ordenador y que se apaga en cuanto la
recibe. La clave no pasa por ningun tercero.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import random
import socket
import threading
import urllib.parse
from dataclasses import dataclass

PAGINA_OK = """<!doctype html><meta charset="utf-8">
<title>Listo</title>
<style>body{font-family:system-ui;text-align:center;padding:4rem;color:#123}
h1{font-size:1.6rem}p{color:#567}</style>
<h1>✅ Clave recibida</h1><p>Ya puedes cerrar esta pestaña y volver a la terminal.</p>"""

PAGINA_MAL = """<!doctype html><meta charset="utf-8">
<title>Algo ha fallado</title>
<style>body{font-family:system-ui;text-align:center;padding:4rem;color:#123}</style>
<h1>No he sabido leer la respuesta</h1>
<p>Vuelve a la terminal, ahí está lo que ha llegado.</p>"""


@dataclass
class Recogida:
    token: str = ""
    privado: str = ""
    crudo: str = ""
    error: str = ""


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def descifrar(bruto: str, sitio: str, passport: float,
              tambien: tuple[str, ...] = ()) -> Recogida:
    """Moodle manda base64 de 'firma:::clave:::clave_privada'.

    La firma es md5(direccion_del_sitio + passport): sirve para comprobar que
    la clave viene del sitio al que se la pedimos y no de otro.
    """
    try:
        texto = base64.b64decode(bruto).decode("utf-8", "replace")
    except Exception:
        return Recogida(crudo=bruto, error="la respuesta no venia en base64")

    partes = texto.split(":::")
    if len(partes) < 2:
        return Recogida(crudo=texto, error="la respuesta no tiene el formato esperado")

    firma, token = partes[0], partes[1]
    privado = partes[2] if len(partes) > 2 else ""

    # Moodle firma con SU direccion canonica. Si hemos entrado por
    # studium.usal.es y nos ha redirigido a studium26.usal.es, la firma buena
    # es la del segundo: hay que comprobar todas las que hemos visto.
    candidatos = [sitio, *tambien]
    esperadas = {hashlib.md5(f"{c.rstrip('/')}{passport}".encode()).hexdigest()
                 for c in candidatos if c}
    if firma not in esperadas:
        return Recogida(token=token, privado=privado, crudo=texto,
                        error="la firma no cuadra: la clave no viene de ese Studium")
    return Recogida(token=token, privado=privado, crudo=texto)


def pedir_con_sesion(sitio: str, sesion: str, passport: float) -> Recogida:
    """Pedir la clave de API usando la sesion que ya tienes abierta.

    Moodle solo acepta como destino un nombre de esquema simple (lo valida y
    devuelve 404 si le pones una direccion entera), asi que no puede
    contestarle a un servidor nuestro. La salida es hacer la peticion desde
    aqui con el identificador de tu sesion y leer la respuesta.

    Ese identificador se usa una sola vez y no se guarda en ningun sitio: la
    clave que se obtiene es permanente, asi que despues da igual que la sesion
    caduque o la cierres.
    """
    import httpx

    import re

    valor = sesion.strip().strip('"\'')
    nombre = "MoodleSession"
    if "=" in valor and valor.split("=", 1)[0].strip().lower().startswith("moodlesession"):
        nombre, valor = (x.strip() for x in valor.split("=", 1))

    # El identificador de sesion de Moodle son 26-32 letras y numeros. Avisar
    # aqui ahorra un viaje entero: lo mas facil es copiar la celda de al lado.
    if not re.fullmatch(r"[A-Za-z0-9]{20,64}", valor):
        pista = ""
        if valor.startswith("http"):
            pista = " Eso es una dirección, no el identificador."
        elif " " in valor:
            pista = " Lleva espacios: has copiado más de una celda."
        elif len(valor) < 20:
            pista = f" Es muy corto ({len(valor)} caracteres)."
        return Recogida(error="eso no parece el valor de MoodleSession." + pista +
                              " Busca la fila «MoodleSession» y copia SOLO la"
                              " columna Value: unas 32 letras y números seguidos.")

    url = (f"{sitio.rstrip('/')}/admin/tool/mobile/launch.php?"
           + urllib.parse.urlencode({"service": "moodle_mobile_app",
                                     "passport": passport,
                                     "urlscheme": "moodlemobile"}))
    # Studium redirige de studium.usal.es al dominio del curso (studium26...)
    # antes de contestar nada. Hay que seguir esos saltos -llevando la sesion-
    # y parar en cuanto aparezca la respuesta de verdad, que no es http sino
    # "moodlemobile://token=...".
    galletas = {nombre: valor}
    destino = ""
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as cli:
            for _ in range(5):
                r = cli.get(url, cookies=galletas)
                siguiente = r.headers.get("location", "")
                if not siguiente:
                    # Sin redireccion: Studium ha contestado con una pagina.
                    # Si es la de identificarse, la sesion no valia.
                    cuerpo = r.text[:4000].lower()
                    if "login" in cuerpo or "identific" in cuerpo:
                        return Recogida(error="esa sesion no vale: Studium responde "
                                              "con la pagina de identificarse. Saca el "
                                              "valor otra vez, recien iniciada la sesion.")
                    destino = ""
                    break
                if "token=" in siguiente or not siguiente.lower().startswith("http"):
                    destino = siguiente
                    break
                # Si nos manda a identificarnos, la sesion ya no sirve. Hay que
                # decirlo aqui: si no, el bucle se come los saltos y acaba
                # diciendo "demasiadas redirecciones", que no ayuda a nadie.
                if "identidad" in siguiente.lower() or "saml" in siguiente.lower():
                    return Recogida(error=(
                        "Studium te manda a identificarte otra vez, asi que esa "
                        "sesion no le vale. Dos motivos posibles: ha caducado, o "
                        "es de OTRO Studium. La USAL tiene uno por curso "
                        "(studium25, studium26...) y cada uno lleva su propia "
                        f"sesion; esta peticion ha ido a {sitio}. Saca el valor "
                        "de la pestana donde VES tus asignaturas."))
                url = str(httpx.URL(url).join(siguiente))
            else:
                return Recogida(error="demasiadas redirecciones")
    except Exception as e:
        return Recogida(error=f"no he podido hablar con Studium: {e}")

    if "token=" not in destino:
        if r.status_code in (302, 303) and ("saml" in destino.lower()
                                            or "identidad" in destino.lower()):
            return Recogida(error="esa sesion ya no vale: Studium pide identificarse "
                                  "otra vez. Saca el valor de nuevo.")
        if r.status_code == 404:
            return Recogida(error="Studium ha rechazado la peticion (404)")
        return Recogida(crudo=destino or f"HTTP {r.status_code}",
                        error="Studium no ha devuelto ninguna clave")

    bruto = urllib.parse.unquote(destino.split("token=", 1)[1].split("&")[0])
    # url es la ultima direccion visitada: de ahi sale el dominio real.
    final = urllib.parse.urlsplit(url)
    canonico = f"{final.scheme}://{final.netloc}"
    return descifrar(bruto, sitio, passport, tambien=(canonico,))


def url_de_lanzamiento(sitio: str, puerto: int, passport: float) -> str:
    # El esquema que le damos a Moodle es en realidad la direccion de nuestro
    # servidor: Moodle pega "://token=..." detras, y sale una direccion http
    # normal que el navegador sabe seguir.
    esquema = f"http://127.0.0.1:{puerto}/clave"
    return (f"{sitio.rstrip('/')}/admin/tool/mobile/launch.php?"
            + urllib.parse.urlencode({
                "service": "moodle_mobile_app",
                "passport": passport,
                "urlscheme": esquema,
            }))


def esperar_clave(sitio: str, puerto: int, passport: float,
                  segundos: int = 300) -> Recogida:
    """Levanta el servidor, espera a que Moodle devuelva la clave y se apaga."""
    resultado = Recogida(error="no ha llegado nada a tiempo")
    recibido = threading.Event()

    class Manejador(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802  (lo exige la libreria)
            nonlocal resultado
            entero = urllib.parse.unquote(self.path)
            if "token=" in entero:
                bruto = entero.split("token=", 1)[1].split("&")[0].strip()
                resultado = descifrar(bruto, sitio, passport)
            else:
                resultado = Recogida(crudo=entero, error="no venia ninguna clave")

            cuerpo = (PAGINA_OK if resultado.token and not resultado.error
                      else PAGINA_MAL).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
            recibido.set()

        def log_message(self, *_args):
            pass  # sin ruido en la terminal

    servidor = http.server.HTTPServer(("127.0.0.1", puerto), Manejador)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        recibido.wait(timeout=segundos)
    finally:
        servidor.shutdown()
        servidor.server_close()
    return resultado
