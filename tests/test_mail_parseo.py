"""El trozo de Gmail que no se puede probar contra Google sin credenciales:
sacar el texto y los adjuntos de la estructura que devuelve la API.
"""

import base64
import unittest

from usalbot.sources.mail import (_adjuntos_de, _partir_remitente, _sin_etiquetas,
                                  _texto_del_cuerpo, es_de_la_usal)


def b64(texto: str) -> str:
    return base64.urlsafe_b64encode(texto.encode()).decode()


MULTIPART = {
    "mimeType": "multipart/mixed",
    "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("La entrega se amplía al día 20.")}},
            {"mimeType": "text/html", "body": {"data": b64("<p>La entrega se amplía</p>")}},
        ]},
        {"mimeType": "application/pdf", "filename": "normas.pdf",
         "body": {"attachmentId": "abc", "size": 1000}},
        {"mimeType": "image/png", "filename": "firma.png", "body": {"size": 20}},
    ],
}

SOLO_HTML = {
    "mimeType": "text/html",
    "body": {"data": b64("<style>p{color:red}</style><p>Hola <b>clase</b></p>")},
}


class TestParseo(unittest.TestCase):
    def test_prefiere_el_texto_plano(self):
        self.assertEqual(_texto_del_cuerpo(MULTIPART), "La entrega se amplía al día 20.")

    def test_html_cuando_no_hay_plano(self):
        self.assertEqual(_texto_del_cuerpo(SOLO_HTML), "Hola clase")

    def test_cuerpo_vacio_no_revienta(self):
        self.assertEqual(_texto_del_cuerpo({}), "")
        self.assertEqual(_texto_del_cuerpo({"mimeType": "text/plain", "body": {}}), "")

    def test_base64_corrupto_no_revienta(self):
        roto = {"mimeType": "text/plain", "body": {"data": "no-es-base64-válido!!"}}
        self.assertIsInstance(_texto_del_cuerpo(roto), str)

    def test_adjuntos_solo_los_de_verdad(self):
        """La firma incrustada no es un adjunto: no tiene attachmentId."""
        self.assertEqual(_adjuntos_de(MULTIPART), ("normas.pdf",))

    def test_no_se_pierde_en_estructuras_hondas(self):
        hondo = {"mimeType": "multipart/mixed", "parts": []}
        nodo = hondo
        for _ in range(20):
            hija = {"mimeType": "multipart/mixed", "parts": []}
            nodo["parts"].append(hija)
            nodo = hija
        nodo["parts"].append({"mimeType": "text/plain", "body": {"data": b64("fondo")}})
        self.assertIsInstance(_texto_del_cuerpo(hondo), str)  # no se cuelga

    def test_quitar_etiquetas(self):
        self.assertEqual(_sin_etiquetas("<script>malo()</script><p>bueno</p>"), "bueno")

    def test_remitente(self):
        self.assertEqual(_partir_remitente('"García, Ana" <ana@usal.es>'),
                         ("García, Ana", "ana@usal.es"))
        self.assertEqual(_partir_remitente("suelto@usal.es"), ("suelto", "suelto@usal.es"))

    def test_dominio_usal(self):
        self.assertTrue(es_de_la_usal("x@usal.es"))
        self.assertTrue(es_de_la_usal("x@estudiantes.usal.es"))
        self.assertFalse(es_de_la_usal("x@usal.es.falso.com"))
        self.assertFalse(es_de_la_usal("x@gmail.com"))


class TestAdjuntosEnElTriaje(unittest.TestCase):
    def test_adjunto_de_la_usal_suma(self):
        from usalbot.models import Mail
        from usalbot.triage import puntuar
        sin = puntuar(Mail(id="1", subject="Info", sender_email="p@usal.es"))
        con = puntuar(Mail(id="2", subject="Info", sender_email="p@usal.es",
                           adjuntos=("normas.pdf",)))
        self.assertEqual(con.score, sin.score + 1)
        self.assertIn("trae 1 adjunto(s)", con.reasons)

    def test_adjunto_de_fuera_no_suma(self):
        from usalbot.models import Mail
        from usalbot.triage import puntuar
        sin = puntuar(Mail(id="1", subject="Info", sender_email="x@gmail.com"))
        con = puntuar(Mail(id="2", subject="Info", sender_email="x@gmail.com",
                           adjuntos=("factura.pdf",)))
        self.assertEqual(con.score, sin.score)


if __name__ == "__main__":
    unittest.main()
