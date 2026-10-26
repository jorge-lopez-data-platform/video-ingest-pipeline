# Bot de Slack por Socket Mode

> **Para quien ejecute:** este plan sustituye el transporte del bot ya
> construido, no la lógica. Lee primero
> `docs/superpowers/plans/2026-10-24-bot-slack-databricks-ejecucion.md`,
> que registra por qué.

**Objetivo:** que el bot reciba eventos de Slack sin exponer una URL HTTP
pública, porque una Databricks App no admite peticiones anónimas.

## El hecho que obliga al cambio

`docs.databricks.com/aws/en/dev-tools/databricks-apps/auth` dice que «both
users and service principals authenticate using standard OAuth 2.0 flows» y
que «Databricks forwards the user's access token to the app». El acceso
programático se documenta solo como autenticación por token. No existe acceso
anónimo.

Slack envía POST sin credenciales de Databricks. El `url_verification` y todos
los eventos morirían en el proxy antes de llegar a Flask.

## Qué se conserva y qué se sustituye

**Se conserva, sin tocar:** `slackbot/nombres.py`, `interpretar.py`,
`acciones.py`, `slack.py` y sus 112 tests. Ninguno depende del transporte.

**Se sustituye:** `slackbot/app.py` (rutas Flask), `app.yaml`,
`slackbot/requirements.txt` → `requirements-bot.txt`, y el uso de
`slackbot/firma.py` en el camino principal (en Socket Mode la autenticación
es el app token; `firma.py` se queda por si algún día vuelve el transporte
HTTP, pero deja de importarse).

**Núcleo nuevo:** `slackbot/nucleo.py`, extraído de los cierres de
`crear_app`. Contiene `atender`, `decir`, `ejecutar`, `cancelar`,
`_predeterminados`, `_texto_consulta`, `enviar_slack` y `llamar_llm_openrouter`,
sin saber nada de HTTP ni de Flask. Recibe las mismas dependencias inyectables
que hoy, para que sus tests no toquen red.

## Tareas

### Task 1: Extraer el núcleo

**Archivos:** crear `slackbot/nucleo.py`; modificar `slackbot/app.py` para que
importe del núcleo en vez de definirlo; tests `tests/slackbot/test_nucleo.py`.

**Interfaz que produce:**

```python
class Nucleo:
    def __init__(self, secretos: dict, deps: dict): ...
    def atender(self, texto: str, canal: str) -> None: ...
    def ejecutar(self, valor: str, canal: str) -> None: ...
    def cancelar(self, valor: str, canal: str) -> None: ...
```

`deps` mantiene las mismas claves que hoy (`interpretar`, `lanzar`,
`leer_job`, `consultar`, `responder`). El registro `usados` sigue viviendo en
la instancia, nunca como global de módulo.

**Criterio:** los 112 tests actuales siguen verdes sin cambios, y `app.py`
solo contiene ya el cableado HTTP.

### Task 2: El consumidor de Socket Mode

**Archivos:** crear `slackbot/socket_mode.py`; crear `requirements-bot.txt`;
borrar `app.yaml` y `slackbot/requirements.txt`; tests
`tests/slackbot/test_socket_mode.py`.

- Lee `SLACK_APP_TOKEN` y `SLACK_BOT_TOKEN` del entorno.
- Abre la conexión con `slack_sdk.socket_mode.SocketModeClient`.
- Traduce cada sobre a las llamadas del `Nucleo`: `app_mention` y `message`
  → `atender`; `block_actions` con `action_id` `ejecutar` o `cancelar` →
  `ejecutar` / `cancelar`.
- Ignora mensajes del propio bot (`bot_id`, `subtype: bot_message`).
- Reconecta con espera creciente si el socket cae; registra solo el tipo de
  error, nunca tokens.

**Tests, sobre la traducción de sobres, no sobre la red:** un sobre de
mención llama a `atender` con su texto y canal; uno de `block_actions`
despacha por `action_id`; uno del propio bot no llama a nada; un sobre
malformado no lanza.

### Task 3: Puesta en marcha

**Archivos:** modificar `docs/operacion-multicanal.md`.

- Documentar que el bot es un proceso que abre conexión saliente, no una app
  web: puerto no requerido.
- Dónde vive el token de app de Slack (`xapp-`), cómo se crea y los scopes que
  hacen falta. Escribir que nunca va en un archivo del repo.
- Sustituir los comandos de despliegue de app por el proceso que sea
  aplicable. Si la decisión es correrlo fuera de Databricks, documentarlo
  explícitamente y decir por qué.

## Review Focus

- Sobres repetidos de Slack: en Socket Mode no llega `X-Slack-Retry-Num`, así
  que la idempotencia del botón descansa solo en `usados`. Un reinicio del
  proceso vuelve a habilitar tarjetas viejas.
- El bot respondiendo a sus propios mensajes en bucle.
- Los tokens de Slack en logs o en el texto de una respuesta.
