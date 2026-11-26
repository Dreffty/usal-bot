# usal-bot

Un bot de Telegram que mira **Studium** y tu **correo de la USAL** por ti y te
dice qué toca, para dejar de entrar asignatura por asignatura a ver si hay algo.

```
tú → ¿qué tengo esta semana?

Esta semana

Viernes 11
  🔴 Práctica 2: sockets — Redes de Computadores  07:32
Sábado 12
  🟠 Test tema 6 — Programación II  05:32
Domingo 13
  🟠 Memoria de laboratorio — Física General  16:32
```

Además te escribe él solo: cuando aparece una tarea nueva, cuando se acerca una
entrega (una semana, tres días, un día y tres horas antes) y cuando llega un
correo que parece importante de verdad.

**Míralo antes de configurar nada:**

```bash
python demo.py
```

---

## Cómo funciona por dentro

| Pieza | Qué hace |
|---|---|
| `usalbot/sources/studium.py` | Habla con Studium por la **API oficial de Moodle** (`moodle_mobile_app`, la misma que usa la app del móvil). Nada de rascar HTML: si cambian el diseño de la web, esto sigue funcionando. |
| `usalbot/sources/mail.py` | Lee el correo. Por defecto con la **API de Gmail** (el correo de la USAL está en Google), y con IMAP si lo prefieres. Solo lectura: el bot no envía ni borra nada. |
| `usalbot/triage.py` | Decide qué correos importan y **explica por qué**, para poder discutírselo cuando se equivoque. |
| `usalbot/resumen.py` | Dice de qué va cada correo importante en dos frases, para no tener que abrirlo. Primero limpia el envoltorio de los avisos de Studium (navegación, enlace, firma, fecha) y saca las frases con información; si hay modelo configurado, pide uno mejor. |
| `usalbot/texto.py` | Comparar texto en castellano sin que las tildes lo rompan: escribes "calculo" y encuentra "Cálculo I". |
| `usalbot/nlu.py` | Entiende preguntas normales sin comandos. Primero con reglas (gratis e instantáneo); solo si no lo ve claro pregunta a un modelo, y únicamente si has configurado uno. |
| `usalbot/db.py` | SQLite. Guarda lo ya visto para no repetir avisos y para responderte al instante sin volver a preguntar a Studium. |
| `usalbot/alerts.py` | Los recordatorios: como mucho cuatro por tarea, cada uno una sola vez. |

La lista de pendientes sale de la **línea de tiempo** de Moodle, que es la que
ves en la portada de Studium. Eso tiene una ventaja: cuando entregas algo, Moodle
lo quita de esa lista, así que el bot se entera solo de que ya está hecho. Lo que
no aparece ahí —entregas sin fecha, o ya hechas— se ve al preguntar por una
asignatura concreta con `/asignatura`.

Con el correo se hacen **dos búsquedas, no una**: primero lo que viene de
`usal.es` o de Studium, con el doble de margen de días, y luego el resto. Así un
correo de hace diez días diciendo "la entrega se amplía al 20" no se queda fuera
por una semana con mucho tráfico. De los de la universidad se baja además el
**cuerpo entero**, porque un asunto vago ("Reunión importante") con el fragmento
corto de Gmail puede colarse sin que nadie lo marque.

---

## Puesta en marcha

Unos 10 minutos, la mayoría esperando a Google.

### 1. Instalar

```bash
cd usal-bot
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python setup_wizard.py init
```

> Con **Python 3.14** hace falta `python-telegram-bot` 22.x o superior: las
> versiones anteriores llaman a `asyncio.get_event_loop()` dando por hecho que
> crea el bucle solo, y en 3.14 eso ya no ocurre. Está fijado en
> `requirements.txt`.

### 2. Crear el bot en Telegram

1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → te da un token.
2. Ponlo en el `.env`: `TELEGRAM_TOKEN=el-token-de-botfather`
3. Averigua tu id sin buscarlo a mano:

```bash
./.venv/bin/python setup_wizard.py telegram
```

Te dirá que le escribas a tu bot; con el mensaje que le mandes ya sabe quién
eres y se guarda tu id solo.

Solo los ids de esa lista pueden hablar con el bot. Cualquier otro recibe una
negativa: aunque alguien descubra el nombre del bot, no ve tus tareas.

### 3. Conectar Studium

```bash
./.venv/bin/python setup_wizard.py studium
```

El asistente pregunta primero a Studium **cómo se entra en él** y se adapta.

En la USAL se entra con **idUSAL por SSO**: tu contraseña la comprueba
`identidad.usal.es`, no Moodle. Eso significa que no hay usuario y contraseña
que darle a Studium — el login por API responde "Acceso inválido" aunque la
escribas bien, porque Moodle sencillamente no la tiene. La clave se crea desde
la web, ya con tu sesión iniciada:

1. Abre `https://studium.usal.es/user/managetoken.php` (entra con idUSAL).
2. Busca la fila del servicio móvil de Moodle, o pulsa **Crear clave de
   seguridad** y elígelo.
3. Copia la clave (32 letras y números) y pégala cuando el asistente la pida, o
   pásala directamente:

```bash
./.venv/bin/python setup_wizard.py studium --token <la-clave>
```

El asistente la prueba en el momento: si vale, te dice tu nombre, cuántas
asignaturas tienes y cuántas cosas pendientes.

**Si esa página no te deja crear la clave** —lo normal para un alumno: hace
falta un permiso que no se da por defecto— quedan dos vías más.

#### Vía B: la clave de API, usando tu sesión del navegador

Moodle tiene una puerta pensada para su app móvil: si ya estás dentro, suelta la
clave sin preguntar. No se le puede pedir que conteste a un servidor local
(valida el destino y solo acepta un nombre de esquema, no una dirección), así
que la petición se hace desde tu ordenador con el identificador de tu sesión:

1. Studium abierto y con sesión iniciada → **F12** → pestaña **Application**
   (Chrome) o **Almacenamiento** (Firefox).
2. **Cookies → https://studium26.usal.es** → fila **MoodleSession** → copia el
   **Value**.

```bash
./.venv/bin/python setup_wizard.py studium --sesion <el-valor>
```

Ese valor se usa **una sola vez**, en tu máquina, y no se guarda en ningún
sitio: lo que queda guardado es la clave de API, que es permanente. Después
puedes cerrar sesión en Studium y anularlo sin que el bot deje de funcionar.

Con esta vía tienes la API completa: el bot sabe qué has entregado y lo quita
solo de la lista, sin `/hecho`.

#### Vía C: exportar tu calendario

1. Studium → **Calendario** → abajo, **Exportar calendario**.
2. Marca **Todos los eventos** y el periodo más largo que te deje ("Este año" o
   "Año próximo"; no "próximos 60 días").
3. **Obtener URL del calendario** y copia la dirección larga.

```bash
./.venv/bin/python setup_wizard.py studium --ical <la-dirección>
```

Ahí están las mismas fechas de entrega, y **se actualizan solas**: esa dirección
no es un fichero descargado, Moodle la genera en vivo cada vez que el bot la
consulta, así que un cambio de última hora se ve en la siguiente pasada igual
que con la API.

Lo único que se pierde es que el calendario **no sabe lo que ya has entregado**,
así que una tarea hecha sigue apareciendo hasta que pase su fecha. Para eso está
`/hecho <nombre>` en el bot. Y al revés: si una tarea deja de salir en Studium
sin que hagas `/hecho` (la entregaste fuera o el profe la quitó), no se borra:
se oculta y la ves con `/entregadas`.

> El otro camino habitual —el flujo de la app móvil, `admin/tool/mobile/launch.php`—
> está roto en Studium: devuelve un error fatal del servidor.

### 4. Conectar el correo (opcional)

```bash
./.venv/bin/python setup_wizard.py gmail
```

La primera vez te explicará cómo sacar el `client_secret.json` de Google Cloud
(cuatro minutos: crear proyecto → activar Gmail API → credenciales de
"aplicación de escritorio"). El permiso que se pide es `gmail.readonly`: leer, y
nada más.

Si prefieres no tocar Google Cloud, pon `MAIL_BACKEND=imap` y rellena
`IMAP_USER` / `IMAP_PASSWORD`. Y si el correo no te interesa, `MAIL_BACKEND=none`.

### 5. Comprobar y arrancar

```bash
./.venv/bin/python setup_wizard.py check   # ¿responde todo?
./.venv/bin/python run.py                  # en marcha
```

---

## Lo que le puedes preguntar

Escríbele como hablarías:

> *¿qué tengo esta semana?* · *¿qué es lo más urgente?* · *¿cuándo se entrega la
> práctica de redes?* · *¿me he perdido algo importante?* · *¿qué tengo de
> cálculo?* · *¿cómo pido el cambio de curso?* · *ponme al día*

O con comandos, si prefieres: `/tareas` `/hoy` `/semana` `/urgente`
`/asignaturas` `/asignatura <nombre>` `/correos` `/clave` `/buscar <texto>`
`/resumen` `/hecho <nombre>` `/entregadas` `/aplazar <nombre> <30m|3h|1d>`
`/pregunta <duda>` `/deshacer` `/sync` `/estado` `/ayuda`

---

## Ajustes del `.env`

| Variable | Para qué |
|---|---|
| `SYNC_MINUTES` | Cada cuánto mira Studium y el correo. 10 por defecto. |
| `SCORE_CLAVE` | Puntos a partir de los cuales un correo cuenta como importante. 7 por defecto. |
| `DAILY_DIGEST_AT` | Hora del parte diario (`08:00`). Déjalo vacío para no recibirlo. |
| `TIMEZONE` | `Europe/Madrid`. |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | Opcional. Para las preguntas raras que las reglas no pillan. Vale cualquier endpoint compatible con OpenAI; el de NVIDIA NIM es gratis. Sin esto el bot funciona igual, solo entiende algo menos. |

---

## Que siga vivo cuando cierres el portátil

En un Mac, con `launchd`:

```bash
cat > ~/Library/LaunchAgents/es.usal.bot.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>es.usal.bot</string>
  <key>ProgramArguments</key>
  <array><string>$PWD/.venv/bin/python</string><string>$PWD/run.py</string></array>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$PWD/bot.log</string>
</dict></plist>
PLIST
launchctl load ~/Library/LaunchAgents/es.usal.bot.plist
```

Para que funcione con el portátil cerrado hace falta que corra en otro sitio: un
Raspberry Pi, un VPS de 3 €, o cualquier máquina encendida. Es un proceso Python
sin base de datos externa, se mueve copiando la carpeta.

---

## Seguridad

- **Todo se queda en tu máquina.** El bot habla con Studium, con Google y con
  Telegram, y con nadie más. La base de datos es un fichero `.db` local.
- **Solo lectura.** Ni entrega tareas, ni envía correos, ni borra nada.
- **El bot es privado**: responde únicamente a los ids de `TELEGRAM_ALLOWED_IDS`.
- El contenido de los correos **se muestra, no se obedece**: el bot nunca hace
  nada porque un correo se lo pida, y al modelo se le dice expresamente que el
  texto que recibe es contenido ajeno y no instrucciones.
- **Qué sale de tu ordenador, exactamente.** Para entender tus preguntas, al
  modelo solo le llega *la pregunta*. Para resumir correos (`RESUMIR_CORREOS=si`,
  por defecto) le llega además **el cuerpo de los correos marcados como
  importantes** — nunca el buzón entero. Con `RESUMIR_CORREOS=no` no sale nada:
  el resumen se saca del propio texto con reglas, aquí mismo.
- `.env`, `token_gmail.json`, `client_secret.json` y los `.db` están en el
  `.gitignore`. No los subas a ningún sitio.

Si quieres cortar el acceso: borra el token en Studium (Preferencias → Seguridad
→ Claves de seguridad) y revoca el permiso de Gmail en
[myaccount.google.com/permissions](https://myaccount.google.com/permissions).

---

## Tests

```bash
./.venv/bin/python -m unittest discover -s tests
```

35 pruebas. Las de `test_integracion.py` recorren el camino completo —de la
respuesta cruda de Moodle al mensaje de Telegram— con las respuestas de la API
imitadas, así que pasan sin credenciales y sin red.

---

## Limitaciones conocidas

- La lista de pendientes sale de la línea de tiempo de Moodle, que solo trae lo
  que tiene fecha. Con la clave de API el bot la **completa con el inventario de
  tus asignaturas** (dos llamadas, no una por asignatura: Moodle acepta la lista
  entera de cursos de una vez), así que las tareas **sin fecha** también
  aparecen — al final de la lista y sin disparar avisos, porque sin plazo no hay
  urgencia. Lo que no se añade nunca es una entrega con fecha futura que no está
  en la línea de tiempo: eso significa que **ya la entregaste**, y meterla sería
  inventarse un pendiente.
- Lo que se anuncie solo de palabra en clase, o solo en un foro sin fecha, no
  está en ninguna de las dos fuentes. El bot cubre Studium y el correo.
- **El bot agrupa y recuerda, no entiende el contenido de las tareas.** Sabe que
  existe "Práctica 2" y cuándo vence; no sabe de qué va. Si le preguntas de qué
  trata, solo tiene el título. Para responder eso haría falta bajarse el
  enunciado de cada tarea, que es otra cosa y no está hecha.
- El modelo de lenguaje, si lo activas, se usa **solo para entender tu pregunta**
  (devuelve qué le estás pidiendo, nada más). Nunca ve el contenido de tu buzón,
  y la respuesta siempre sale de tus datos, no del modelo.
- El triaje de correos son reglas, no magia. El umbral (`UMBRAL_CLAVE` en
  `usalbot/models.py`) está calibrado contra un buzón real de la USAL, donde
  marca como importante 8 de cada 17 correos: matrícula, cambios de grupo y
  notas entran; boletines internos y bienvenidas, no. Si te resulta charlatán,
  sube el número; si se deja cosas, bájalo. `/clave` te dice siempre por qué
  marcó cada uno.
- Si **mueven la fecha** de una entrega, te avisa en la siguiente pasada, y
  distingue si te la han adelantado (peor noticia) de si te la han ampliado.
- La primera vez que arranca **no te avisa de todo lo atrasado**: toma nota de
  lo que hay, te manda un único resumen, y a partir de ahí solo avisa de lo que
  cambie.
