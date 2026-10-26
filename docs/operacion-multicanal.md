# Operación del pipeline multicanal

Job `tiktok_produccion_diaria` (id `813880207704409`), workspace
`https://dbc-e83fc5f3-f80b.cloud.databricks.com`.

Genera 1 video por corrida y lo distribuye a TikTok (borrador), YouTube Shorts
y Facebook Reels.

## Cadencia

`0 0 2,8,14,20 * * ?` — 4 corridas al día en UTC.

**Por qué cuatro y no seis:** TikTok limita a **5 subidas al inbox por ventana
móvil de 24 h** (`spam_risk_too_many_pending_share`). A seis diarias, una de
cada seis se perdía por construcción. Cuatro deja margen para reintentos.

## Cupo del inbox: el techo real

TikTok documenta en `inbox/video/init/`: «there may be at most 5 pending shares
within any 24-hour period». El cupo cuenta **borradores pendientes**, no
subidas: se libera cuando el dueño publica o borra desde la app.

Medido el 2026-10-24 en `tiktok.bronze.tiktok_uploads`: **33 de 40 intentos**
fueron `SKIPPED_INBOX_FULL` (82 %). La causa no era el cupo en sí, sino el lazo
que lo rodeaba:

| Defecto | Efecto | Arreglo |
|---|---|---|
| `allow_resubmit=true` | Desactivaba el SHA-256; el mismo archivo se reenviaba | `false` |
| `max_retries=3` en `subir_borrador` | 3 reintentos de un error determinista | `0` |
| `max_pendientes=2`, orden ascendente | Re-encolaba los pendientes más viejos, ya fallidos | `1`, orden descendente, excluye saltados <24 h |
| Sin comprobación previa de cupo | 14 `init` en 4 min con cupo agotado | `scripts/cuota.py` corta antes de la red |

**Para llegar a 20/día hay que salir del inbox.** El cupo de 5/24 h no se puede
subir desde el código. La vía es Direct Post (`post/publish/video/init/`), cuya
task `publicar_tiktok_directo` ya está en el DAG, **desactivada** hasta que la
app «Lomasviral» pase la auditoría de Content Posting. Hoy responde
`unaudited_client_can_only_post_to_private_accounts`.


## DAG

```
guiones_60s → generar_video ─┬→ listar_pendientes → subir_borradores ─┐
                             ├→ publicar_youtube ────────────────────┤
                             └→ publicar_facebook ───────────────────┴→ cerrar_distribucion
```

`listar_pendientes` corre **antes** de la subida y compone la cola: los videos
nuevos más hasta `max_pendientes` (=2) pendientes de corridas anteriores.

## Los tres canales

| Canal | Modo | Requisito |
|---|---|---|
| TikTok | Borrador en el inbox | Publicar a mano desde la app |
| YouTube | Privado | Auditoría de Google para pasar a público |
| Facebook | Público | Token de página de «Alguien que te escucha» |

### TikTok

Sube a `inbox/video/init/`, que solo necesita el scope `video.upload`. El video
queda como borrador; **hay que abrirlo en la app y publicarlo**. Si no se
publica, no pasa nada: el cupo cuenta subidas, no borradores acumulados.

Los videos saltados se reintentan solos en corridas posteriores.

### YouTube

`videos.insert` en modo resumable. Los proyectos de Google Cloud creados tras
2020-07-28 suben **todo en privado** hasta pasar la auditoría
(Cloud Console → Pantalla de consentimiento OAuth → Enviar para verificación).
Mientras tanto, `privacy=private` en el job es lo correcto; cambiarlo a
`public` no tendría efecto.

Cuota: 10 000 unidades/día, 1 600 por subida → 6 videos/día como techo teórico,
pero el límite que manda hoy es el de TikTok.

### Facebook

Reels en la página **«Alguien que te escucha»** (id `101563571995144`,
`facebook.com/alguienqueteescucha`). Ese id es el que `/me/accounts` devuelve con
permisos para publicar. **MCK ZONE (`104360577922877`) era el nombre anterior de
la misma página**: cualquier referencia a ese id en el código o en notas viejas
está obsoleta.

**El token caduca, y este es el punto frágil del sistema:**

| Origen | Duración |
|---|---|
| Explorer, token de usuario | ~1 hora |
| Explorer, token de página | ~1 hora |
| `/me/accounts` con token de usuario de 60 días | **sin caducidad** |

El objetivo, por tanto, no es «renovar a menudo» sino **llegar una sola vez a un
token de página que no caduque**. Los dos canjes que lo consiguen son:

1. `GET /oauth/access_token` con `grant_type=fb_exchange_token` — token corto de
   usuario → token de usuario de 60 días.
2. `GET /me/accounts` con ese token → `access_token` de la página, **permanente**.

Para hacerlo, en una ventana de PowerShell normal (no desde Claude Code, que
manda a segundo plano los comandos que esperan entrada):

```powershell
cd C:\Users\lopee\tiktok-pipeline
powershell -ExecutionPolicy Bypass -File jobs\canjear_token_facebook.ps1
```

Pide el **App Secret** y un token de **usuario** corto del Explorer. Antes de
guardar nada comprueba dos cosas con `POST /{page_id}/video_reels`: que
`debug_token` devuelva `expires_at = 0` (sin caducidad) y que la sesión de
publicación abra. Si la caducidad no es 0, **aborta sin escribir en el scope** —
así el job nunca vuelve a usar un token muerto.

`jobs/guardar_token_pagina.ps1` es la versión corta: salta el canje 1 y solo
deriva el token de página de uno de usuario ya largo. Sirve para repetir el paso
2, no para conseguir la permanencia desde cero.

**`/me/accounts` devuelve `{"data": []}` aunque todo esté bien.** Con la Nueva
Experiencia de Páginas, la lista puede venir vacía mientras `debug_token`
demuestra que los cuatro scopes están concedidos sobre la página correcta. No es
un fallo de permisos ni de rol: por eso `canjear_token_facebook.ps1` tiene un
segundo camino que pide el `access_token` por id directo
(`GET /{page_id}?fields=access_token`). Si ambos fallan ahí sí falta algo, casi
siempre `business_management` por estar la página en un portafolio empresarial.

## Secretos

Scope `tiktok_pipeline`:

| Clave | Qué es |
|---|---|
| `youtube_client_id` | Cliente OAuth de Google |
| `youtube_client_secret` | Secreto del mismo cliente (`GOCSPX-`) |
| `youtube_refresh_token` | Refresh token (`1//`) |
| `fb_page_id` | `101563571995144` («Alguien que te escucha») |
| `fb_page_access_token` | Token de **página** |
| `fb_graph_version` | `v26.0` |

Ningún token se imprime nunca. Para verificar YouTube sin exponerlo:

```
python -I C:\Users\lopee\AppData\Local\Temp\verificar_youtube.py
```

## Problemas conocidos

**`SKIPPED_INBOX_FULL` en masa.** El cupo de 5/24 h está gastado. Es normal tras
una ráfaga; se resuelve esperando. Si es sistemático, revisa que el cron no
supere 4/día.

**`start_400` en Facebook.** Token caducado (código 190, subcódigo 463). Ejecuta
`canjear_token_facebook.ps1` para obtener uno permanente desde cero.

**Nombres `_v2`.** Los renders antiguos se guardaron como `<base>_v2.mp4`
mientras `gold.guiones_usados` usa el nombre canónico. `listar_pendientes`
normaliza ambos lados; si añades código que compare nombres, haz lo mismo o
re-subirás videos ya publicados.

**Dos eras de carpetas.** Los renders nuevos se guardan en
`videos/AAAA/MM/DD/` junto a un `descripciones_reels.md` del lote; los antiguos
están planos en la raíz del volumen. Nunca concatenes `VIDEOS_DIR + nombre`:
usa `rutas.resolver()` (y `rutas.companero()` para el `.txt`), que mira ambas.
El nombre del mp4 no cambia en ninguna era.

**`timeout_seconds` o `environment_key` en una tarea `for_each`.** La API los
rechaza en la tarea exterior. Van solo en la tarea interior.

## Tablas

| Tabla | Contenido |
|---|---|
| `tiktok.gold.guiones_usados` | Todo lo generado |
| `tiktok.bronze.tiktok_uploads` | Lo que llegó (o no) al inbox |
| `tiktok.bronze.distribucion_video` | Estado por (video, canal) |

## Ficheros

| Ruta | Para qué |
|---|---|
| `jobs/multicanal.job.json` | Definición del job |
| `jobs/reordenar_pendientes.py` | Recablea el DAG |
| `jobs/ajustar_cadencia_tiktok.py` | Cron y `max_pendientes` |
| `jobs/despausar.py` | Genera el payload de despausado |
| `docs/superpowers/specs/2026-10-24-bot-slack-databricks-design.md` | Diseño del bot de Slack (lenguaje natural → jobs) |
| `jobs/guardar_token_pagina.ps1` | Renueva el token de Facebook |
| `jobs/set_youtube_secrets.ps1` | Carga y verifica credenciales de Google |
| `pendientes_borradores.py` | Notebook de la cola (desplegado en el workspace) |
| `scripts/rutas.py` | Resuelve mp4 en la raíz y en `AAAA/MM/DD` |
| `scripts/descripciones.py` | Compone el `descripciones_reels.md` del lote |

Los dos módulos de `scripts/` se copian al volumen (`videos/lib/scripts/`) con
`databricks fs cp --overwrite`; los notebooks los importan desde ahí.

## Bot de Slack

Proceso `slackbot/socket_mode.py` (código en `slackbot/`). Recibe un mensaje en
lenguaje natural, lo interpreta con un LLM y lanza o consulta jobs del
pipeline. Diseño y planes:
[spec](superpowers/specs/2026-10-24-bot-slack-databricks-design.md) ·
[plan original](superpowers/plans/2026-10-24-bot-slack-databricks.md) ·
[plan de Socket Mode](superpowers/plans/2026-10-25-bot-slack-socket-mode.md).

### Qué es: un proceso de conexión saliente

El bot **no es una app web**. Abre una conexión WebSocket saliente hacia Slack
(Socket Mode) y recibe por ella los eventos y los clics de botón. Por eso **no
necesita URL pública ni ingress**: basta con que la máquina pueda salir a
internet por HTTPS. El puerto solo hace falta para que la plataforma no dé la
app por caída (ver «Puesta en marcha»).

Motivo del cambio: la versión anterior era una Databricks App con Flask. Una
Databricks App exige OAuth 2.0 en todo el acceso y no admite peticiones
anónimas; Slack envía sus POST sin credenciales de Databricks, así que el
`url_verification` y los eventos morían en el proxy. `slackbot/app.py` (HTTP)
sigue en el repo pero **ya no se despliega**: `app.yaml` arranca
`slackbot.socket_mode`, no `slackbot.app`. La autenticación del origen ya no es
la firma de Slack sino el token de app.

### Acciones

| Acción | Qué hace | Estado |
|---|---|---|
| `lanzar_lote` | Lanza un run del job de lotes con los parámetros indicados | Operativa |
| `consultar_estado` | Consulta el estado de un run | Operativa |
| `listar_pendientes` | Lista videos pendientes de subir | Operativa |
| `estado_videos` | Estado de videos por canal | Operativa |

Las dos leen con `statement_execution` a través de `slackbot/consultas.py`:

| Acción | Tablas |
|---|---|
| `listar_pendientes` | `gold.guiones_usados` menos `bronze.tiktok_uploads` |
| `estado_videos` | las mismas, más `bronze.distribucion_video` (YouTube/Facebook) |

`guiones_usados` **no tiene columna `canal`**: filtrar por canal ahí rompería la
consulta, así que el filtro `canal` solo se aplica a `distribucion_video`. Si
pides `estado_videos` de un solo canal, el bloque de TikTok sigue saliendo
completo; solo se recorta la parte por canal.

El warehouse se toma de la variable `SQL_WAREHOUSE_ID` y, si no está, del
primero visible para el service principal. El tope es de 500 filas por consulta.

### Flujo

mensaje → interpretación → **tarjeta de confirmación** → botón **Ejecutar**.

La tarjeta muestra los parámetros entendidos (`cantidad`, `hashtags`,
`min_likes`...) y marca «por defecto del job» los que no se indicaron. Mientras
no se pulse Ejecutar no se crea ningún run. El proceso confirma («ack») cada
sobre antes de trabajar, ignora los mensajes del propio bot y atiende una sola
vez una mención que llega como `app_mention` y como `message`.

La idempotencia del botón vive en memoria del proceso: **un reinicio vuelve a
habilitar tarjetas viejas**. Tras reiniciar, pulsa solo tarjetas recientes.

**Instagram queda fuera.** Los canales soportados son los tres del pipeline
(TikTok, YouTube, Facebook); una petición que mencione Instagram no se ejecuta.

### Tokens de Slack

Los crea una persona en la configuración de la app de Slack
(`api.slack.com/apps`). Hacen falta dos:

1. **Token de app (`xapp-`)**. En *Basic Information* > *App-Level Tokens* >
   *Generate Token and Scopes*, con el scope **`connections:write`**. Es el que
   permite abrir la conexión de Socket Mode. Además, en *Socket Mode* activa
   *Enable Socket Mode*.
2. **Token de bot (`xoxb-`)**. Se obtiene al instalar la app en el espacio de
   trabajo. Sus scopes son `app_mentions:read` (para las menciones),
   `im:history` (para los mensajes directos) y `chat:write` (para responder y
   publicar las tarjetas). En *Event Subscriptions* suscribe **`app_mention` y
   `message.im`**, no `message.*`; sin Request URL, que Socket Mode no la usa.
   Activa *Interactivity* para los botones. Si cambias scopes, reinstala la app
   para que el token los incluya.

**Por qué `message.im` y no `message.*`:** `message.channels` entrega **toda**
la conversación de cada canal donde esté el bot, mencione o no. Cada línea de
charla costaría una llamada al LLM y una respuesta «no te entendí» en medio de
la conversación de otras personas. El código lo filtra igual —
`slackbot/socket_mode.py` solo atiende un `message` si viene con
`channel_type == "im"`—, pero suscribir de menos ahorra el tráfico y el riesgo.

**Nunca va en un archivo del repo**: ni en `.env`, ni en un script, ni en la
documentación. El proceso los lee del entorno o del scope y nunca los registra;
ante un fallo loguea únicamente el tipo de excepción.

### Dónde se guarda cada token

| Variable de entorno del proceso | Qué es | Custodia |
|---|---|---|
| `SLACK_APP_TOKEN` | Token de app `xapp-` | Scope `tiktok_pipeline`, clave `slack_app_token` |
| `SLACK_BOT_TOKEN` | Token de bot `xoxb-` | Scope `tiktok_pipeline`, clave `slack_bot_token` |
| `OPENROUTER_API_KEY` (opcional) | Clave del LLM | Scope `tiktok_pipeline`, clave `openrouter_api_key` |
| `OPENROUTER_MODEL` (opcional) | Modelo; por defecto `anthropic/claude-sonnet-4.5` | No es secreto |

Para cada una de las tres, **el código mira primero la variable de entorno y,
si no está, la lee del scope** (`_secreto_o_entorno`). En una Databricks App
nadie exporta nada: el contenedor arranca solo, así que en producción los
valores llegan del scope. El entorno sirve para depurar en local.

El service principal de la app necesita **READ** sobre el scope, o la lectura
falla y el proceso sale con `no se pudo leer SLACK_APP_TOKEN (ni en el entorno
ni en el scope): <TipoDeError>`. El mensaje nombra la variable y **el tipo** de
error, nunca el valor: así se distingue «la clave no existe» de «falta READ»
(`PermissionDenied`) sin tener que adivinar.

Alta de los secretos, a mano. El valor se introduce por entrada estándar cuando
el comando lo pide; **nunca se escribe en la línea de comandos ni en un
archivo**:

```
databricks secrets put-secret tiktok_pipeline slack_app_token --profile DEFAULT
databricks secrets put-secret tiktok_pipeline slack_bot_token --profile DEFAULT
databricks secrets put-secret tiktok_pipeline openrouter_api_key --profile DEFAULT
```

`slack_signing_secret` **no hace falta en Socket Mode**: solo lo usa
`slackbot/firma.py`, que ya no está en el camino de entrada. Solo se necesita
si algún día vuelve el transporte HTTP.

Lectura para exportarlos en la sesión (PowerShell; el CLI devuelve el valor en
base64):

```
$claves = @{ SLACK_APP_TOKEN = 'slack_app_token'; SLACK_BOT_TOKEN = 'slack_bot_token'; OPENROUTER_API_KEY = 'openrouter_api_key' }
foreach ($par in $claves.GetEnumerator()) {
  $b64 = (databricks secrets get-secret tiktok_pipeline $par.Value --profile DEFAULT | ConvertFrom-Json).value
  Set-Item -Path "Env:$($par.Key)" -Value ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)))
}
```

### Puesta en marcha

**Decisión: el proceso corre en una Databricks App**, la misma que ya existía.
Cambiar el transporte no cambia dónde vive.

Por qué una App y no un job: es un bucle que no termina (mantiene la conexión y
reconecta con espera creciente de 1 s hasta 60 s). Un job está hecho para tareas
que acaban, y mataría el proceso al terminar la tarea. Una App mantiene un
contenedor vivo mientras la app esté en marcha, que es lo que hace falta.

Por qué la App sirve aunque no admita peticiones anónimas: Slack ya no envía
peticiones entrantes. La conexión la abre el bot hacia fuera (Socket Mode), y
eso es tráfico saliente normal, que la App permite.

**El único requisito que impone la plataforma:** una App *debe* escuchar en
`DATABRICKS_APP_PORT`. Si nada se enlaza a ese puerto, la plataforma da la app
por caída y la reinicia. Por eso `slackbot/socket_mode.py` no es solo el bucle:
`main()` levanta un servidor de salud mínimo en ese puerto, en un hilo daemon,
y el bucle del socket corre en el hilo principal. Ese servidor no expone nada
del bot; responde **200 a cualquier ruta**, porque un 404 también cuenta como
caída. En local usa el 8000 si `DATABRICKS_APP_PORT` no existe.

`app.yaml` fija el comando de arranque (`python -m slackbot.socket_mode`).

**No despliegues la raíz del repo.** Una App instala el `requirements.txt` que
haya en la raíz del código fuente; el de este repo es el del pipeline de vídeo
(`faster-whisper`, `imageio-ffmpeg`) y **no incluye `slack_sdk`**, así que el
bot no arrancaría. `jobs/desplegar_bot.ps1` compone el paquete con solo lo del
bot —`app.yaml`, `slackbot/*.py` y `requirements-bot.txt` copiado como
`requirements.txt`—, lo sube y despliega.

**El paquete se compone en `%TEMP%`, fuera del repo, y no es un capricho:**
`databricks sync` respeta el `.gitignore` del repositorio que contiene el
directorio de origen. Un `build_bot/` dentro del repo se sube **vacío** —
`Initial Sync Complete`, cero ficheros — y `apps deploy` publicaría una app sin
código. El script comprueba que `app.yaml` llegó antes de desplegar.

Despliegue, en este orden:

```
databricks apps create tiktok-slack-bot --profile DEFAULT
powershell -ExecutionPolicy Bypass -File jobs\desplegar_bot.ps1
```

La identidad que ejecuta el proceso en la App es **siempre el service
principal de la app** (no el usuario que despliega). Necesita:

- **READ** sobre el scope `tiktok_pipeline` — sin esto no puede leer los tokens
  y sale con `no se pudo leer … : PermissionDenied`.
- **CAN_MANAGE_RUN** sobre el job `638404369584915` — para lanzar runs y
  consultar su estado.
- **CAN_USE** sobre el SQL warehouse — `listar_pendientes` y `estado_videos`
  ejecutan SQL con `statement_execution`. Sin esto responden
  `No pude hacer la consulta (RuntimeError)`.
- **SELECT** sobre `tiktok.gold.guiones_usados`, `tiktok.bronze.tiktok_uploads`
  y `tiktok.bronze.distribucion_video`. Se concede en el catálogo, no en la app.

Se conceden en la pestaña *Permissions* de la app.

Los logs salen por stdout:

```
databricks apps logs tiktok-slack-bot --profile DEFAULT
```

Ahí aparece `socket mode conectado` al abrir la conexión. Ante un fallo se
registra el tipo de error, nunca el token. Tras un cambio de código,
`databricks apps deploy` otra vez.

**No despliegues dos copias** (dos apps, o una app y una máquina local): ambas
abrirían conexión y atenderían el mismo mensaje dos veces.

Para depurar sin desplegar, en local desde la raíz del repo:
`python -m slackbot.socket_mode`. Las variables de entorno son opcionales: lo
que falte se lee del scope con el perfil activo. El servidor de salud usa el
8000 cuando `DATABRICKS_APP_PORT` no existe.

`requirements-bot.txt` es solo del bot; el `requirements.txt` de la raíz es del
pipeline de video y no se mezcla.

### `max_pendientes`

Si el lote incluye TikTok, el bot lo fija a 5 (cupo del inbox por 24 h) en vez
de heredar el `0` del job, que significa «todos».
