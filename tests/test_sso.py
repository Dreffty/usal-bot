"""Sacar la clave de API aprovechando una sesión ya iniciada."""

import base64
import hashlib
import unittest
from unittest import mock

from usalbot.sso_token import descifrar, pedir_con_sesion, url_de_lanzamiento

SITIO = "https://studium26.usal.es"
PASSPORT = 1234.5678
# Con el formato real: 32 caracteres hexadecimales.
SESION = "55ec6e167d7bb3ab48b252b93b942cd1"


def firma_buena(sitio=SITIO, passport=PASSPORT):
    return hashlib.md5(f"{sitio}{passport}".encode()).hexdigest()


def respuesta(token="a" * 32, privado="b" * 32, firma=None):
    firma = firma_buena() if firma is None else firma
    return base64.b64encode(f"{firma}:::{token}:::{privado}".encode()).decode()


class ClienteFalso:
    """Imita httpx.Client: devuelve los pasos que se le den, en orden."""

    def __init__(self, pasos):
        self.pasos = pasos
        self.urls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url, cookies=None):
        self.urls.append(url)
        self.cookies = cookies
        estado, cabeceras, cuerpo = self.pasos[min(len(self.urls) - 1,
                                                   len(self.pasos) - 1)]
        return mock.Mock(status_code=estado, headers=cabeceras, text=cuerpo)


def con_pasos(pasos):
    cliente = ClienteFalso(pasos)
    return mock.patch("httpx.Client", lambda **kw: cliente), cliente


class TestDescifrado(unittest.TestCase):
    def test_respuesta_buena(self):
        r = descifrar(respuesta(), SITIO, PASSPORT)
        self.assertEqual(r.token, "a" * 32)
        self.assertEqual(r.privado, "b" * 32)
        self.assertEqual(r.error, "")

    def test_firma_que_no_cuadra(self):
        """Si la firma no coincide, la clave no viene del sitio que creemos."""
        r = descifrar(respuesta(firma="firmafalsa"), SITIO, PASSPORT)
        self.assertIn("no viene de ese Studium", r.error)

    def test_otro_passport_invalida(self):
        self.assertTrue(descifrar(respuesta(), SITIO, 9999.0).error)

    def test_sin_clave_privada(self):
        bruto = base64.b64encode(f"{firma_buena()}:::soloclave".encode()).decode()
        r = descifrar(bruto, SITIO, PASSPORT)
        self.assertEqual(r.token, "soloclave")
        self.assertEqual(r.privado, "")

    def test_basura(self):
        self.assertTrue(descifrar("no-es-base64-@@@", SITIO, PASSPORT).error)
        self.assertTrue(descifrar(base64.b64encode(b"sinseparadores").decode(),
                                  SITIO, PASSPORT).error)

    def test_la_barra_final_del_sitio_no_importa(self):
        self.assertEqual(descifrar(respuesta(), SITIO + "/", PASSPORT).error, "")


class TestPeticion(unittest.TestCase):
    def test_clave_recibida(self):
        parche, _ = con_pasos([(303, {"location": f"moodlemobile://token={respuesta()}"}, "")])
        with parche:
            r = pedir_con_sesion(SITIO, SESION, PASSPORT)
        self.assertEqual(r.token, "a" * 32)
        self.assertEqual(r.error, "")

    def test_sigue_el_cambio_de_dominio(self):
        """studium.usal.es responde 301 a studium26 ANTES de contestar nada.

        Leer ese primer Location devolvía la dirección del propio Studium en
        vez de la clave: el flujo fallaba siempre, con sesión buena o mala.
        """
        parche, cliente = con_pasos([
            (301, {"location": "https://studium26.usal.es/admin/tool/mobile/launch.php?x=1"}, ""),
            (303, {"location": f"moodlemobile://token={respuesta()}"}, ""),
        ])
        with parche:
            r = pedir_con_sesion("https://studium.usal.es", SESION, PASSPORT)
        self.assertEqual(len(cliente.urls), 2, "tenía que seguir el 301")
        self.assertEqual(r.token, "a" * 32)

    def test_sesion_caducada_manda_al_sso(self):
        parche, _ = con_pasos([(303, {"location": "https://identidad.usal.es/adas/SAML2/x"}, "")])
        with parche:
            r = pedir_con_sesion(SITIO, SESION, PASSPORT)
        self.assertIn("no le vale", r.error)
        self.assertEqual(r.token, "")

    def test_el_aviso_nombra_el_sitio_al_que_fue(self):
        """La USAL tiene un Studium por curso y es fácil equivocarse de año:
        el mensaje tiene que decir a cuál se ha preguntado."""
        parche, _ = con_pasos([(303, {"location": "https://identidad.usal.es/x"}, "")])
        with parche:
            r = pedir_con_sesion("https://studium26.usal.es", SESION, PASSPORT)
        self.assertIn("studium26.usal.es", r.error)
        self.assertIn("uno por curso", r.error)

    def test_pagina_de_login_se_explica(self):
        parche, _ = con_pasos([(200, {}, "<html>Login to Studium</html>")])
        with parche:
            r = pedir_con_sesion(SITIO, SESION, PASSPORT)
        self.assertIn("no vale", r.error)

    def test_404_se_explica(self):
        parche, _ = con_pasos([(404, {}, "no existe")])
        with parche:
            r = pedir_con_sesion(SITIO, SESION, PASSPORT)
        self.assertIn("404", r.error)

    def test_bucle_de_redirecciones(self):
        parche, _ = con_pasos([(301, {"location": "https://studium26.usal.es/a"}, "")])
        with parche:
            r = pedir_con_sesion(SITIO, SESION, PASSPORT)
        self.assertIn("redirecciones", r.error)

    def test_acepta_el_valor_con_nombre_delante(self):
        """Da igual pegar 'abc123' que 'MoodleSession=abc123'."""
        parche, cliente = con_pasos([(303, {"location": f"moodlemobile://token={respuesta()}"}, "")])
        with parche:
            pedir_con_sesion(SITIO, f"MoodleSession={SESION}", PASSPORT)
        self.assertEqual(cliente.cookies, {"MoodleSession": SESION})

    def test_studium_caido(self):
        with mock.patch("httpx.Client", side_effect=OSError("sin red")):
            r = pedir_con_sesion(SITIO, SESION, PASSPORT)
        self.assertIn("no he podido hablar", r.error)


class TestUrl(unittest.TestCase):
    def test_lleva_lo_que_moodle_necesita(self):
        u = url_de_lanzamiento(SITIO, 8765, PASSPORT)
        self.assertIn("/admin/tool/mobile/launch.php?", u)
        self.assertIn("service=moodle_mobile_app", u)
        self.assertIn("1234.5678", u)


if __name__ == "__main__":
    unittest.main()


class TestValorPegadoMal(unittest.TestCase):
    """Lo más fácil es copiar la celda equivocada; que lo diga antes de salir."""

    def _error(self, valor):
        return pedir_con_sesion(SITIO, valor, PASSPORT).error

    def test_una_direccion(self):
        self.assertIn("dirección", self._error("https://studium26.usal.es/my/"))

    def test_varias_celdas(self):
        self.assertIn("más de una celda", self._error(f"MoodleSession {SESION}"))

    def test_demasiado_corto(self):
        self.assertIn("muy corto", self._error("abc123"))

    def test_el_bueno_pasa(self):
        parche, _ = con_pasos([(303, {"location": f"moodlemobile://token={respuesta()}"}, "")])
        with parche:
            r = pedir_con_sesion(SITIO, SESION, PASSPORT)
        self.assertEqual(r.error, "")

    def test_con_comillas_alrededor(self):
        """Copiar de DevTools a veces arrastra comillas."""
        parche, _ = con_pasos([(303, {"location": f"moodlemobile://token={respuesta()}"}, "")])
        with parche:
            r = pedir_con_sesion(SITIO, f'"{SESION}"', PASSPORT)
        self.assertEqual(r.error, "")


class TestFirmaConRedireccion(unittest.TestCase):
    """Moodle firma con SU dirección canónica.

    Entrando por studium.usal.es y siendo redirigido a studium26, la firma
    buena es la del segundo. Comprobar solo contra la primera daba "la firma
    no cuadra" con una clave perfectamente legítima.
    """

    def test_la_firma_del_dominio_final_vale(self):
        canonico = "https://studium26.usal.es"
        firma = hashlib.md5(f"{canonico}{PASSPORT}".encode()).hexdigest()
        carga = base64.b64encode(f"{firma}:::{'c' * 32}:::x".encode()).decode()

        parche, _ = con_pasos([
            (301, {"location": f"{canonico}/admin/tool/mobile/launch.php?x=1"}, ""),
            (303, {"location": f"moodlemobile://token={carga}"}, ""),
        ])
        with parche:
            r = pedir_con_sesion("https://studium.usal.es", SESION, PASSPORT)
        self.assertEqual(r.token, "c" * 32)
        self.assertEqual(r.error, "", "la firma del dominio final debería valer")

    def test_una_firma_de_otro_sitio_sigue_sin_valer(self):
        carga = base64.b64encode(
            f"{'0' * 32}:::{'c' * 32}:::x".encode()).decode()
        parche, _ = con_pasos([(303, {"location": f"moodlemobile://token={carga}"}, "")])
        with parche:
            r = pedir_con_sesion(SITIO, SESION, PASSPORT)
        self.assertIn("no viene de ese Studium", r.error)
