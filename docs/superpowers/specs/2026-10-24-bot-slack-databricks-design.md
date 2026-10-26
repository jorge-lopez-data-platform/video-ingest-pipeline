# Bot de Slack para operar el pipeline en lenguaje natural

Fecha: 2026-10-24
Estado: aprobado para planificación

## Objetivo

Poder dar órdenes al pipeline desde Slack en lenguaje natural —«lanza un job de
10 videos con estos hashtags, que tengan más de 100 000 likes, súbelos a TikTok
y Facebook»— y saber en cualquier momento **qué videos se subieron y cuáles no**,
sin abrir la interfaz de Databricks.

## Fuera de alcance

- **Instagram.** El pipeline publica hoy en TikTok, YouTube y Facebook. Si se pide
  Instagram, el bot responde que ese canal no existe todavía; no finge haberlo
  hecho. Añadirlo es un proyecto aparte (cuenta vinculada, permiso
  `instagram_content_publish`, notebook de publicación).
- **Modificar la configuración de los jobs.** El bot lanza jobs con parámetros
  de corrida y lee estado; no reescribe definiciones, cron ni secretos.
- **Consulta libre de tablas.** El bot solo responde las preguntas para las que
  tiene una acción declarada.

## Decisiones tomadas

| Decisión | Elección | Motivo |
|---|---|---|
| Dónde vive | Databricks App | Tokens en el secret scope, sin depender de una máquina encendida |
| Libertad del LLM | Herramientas fijas con parámetros tipados | El modelo no puede inventar jobs ni claves del payload |
| Confirmación | Siempre, con botón en Slack | Una mala interpretación no se convierte en 10 videos publicados |
| Instagram | Fuera | No existe en el pipeline |

## Arquitectura

Cuatro piezas, cada una con una responsabilidad y una interfaz clara.

```
Slack  ──evento──▶  app.py  ──texto──▶  interpretar.py  ──acción──▶  app.py
                      │                                                  │
                      │◀──────────── tarjeta de confirmación ────────────┘
                      │
                 clic en botón
                      │
                      ▼
                  acciones.py ──▶ API Jobs / SQL Warehouse
                      │
                      ▼
                  slack.py ──▶ mensaje con el resultado y el enlace al run
```

### `app.py` — la única superficie pública

Databricks App (Python) con dos rutas:

- `POST /slack/events` — mensajes del canal. Responde el `challenge` del
  handshake inicial y acusa recibo en menos de 3 s (Slack reintenta si no).
  El trabajo real se hace en un hilo para no bloquear la respuesta.
- `POST /slack/interactions` — clics en los botones.

**Verificación de firma obligatoria en ambas rutas**, con el `signing_secret`
de Slack y la cabecera `X-Slack-Signature` (HMAC-SHA256 sobre
`v0:{timestamp}:{body}`), rechazando peticiones de más de 5 minutos de antigüedad.
Sin esto, cualquiera con la URL podría lanzar jobs que gastan dinero y publican.
Los reintentos de Slack se descartan por `X-Slack-Retry-Num` para no duplicar
órdenes.

### `interpretar.py` — texto a acción tipada

Una llamada a OpenRouter con `response_format` de JSON Schema y
`temperature=0`. La clave es `openrouter_api_key`, que ya vive en el scope
`tiktok_pipeline` para el resto del pipeline; no se añade una segunda.

El esquema declara **cuatro acciones** más un caso de fallo explícito:

```jsonc
{
  "accion": "lanzar_lote" | "consultar_estado" | "listar_pendientes" |
            "estado_videos" | "no_entendido",
  "parametros": { ... }   // según la acción
}
```

| Acción | Parámetros | Efecto |
|---|---|---|
| `lanzar_lote` | `cantidad` (1–20), `hashtags` (lista), `min_likes` (0–10 000 000), `top_n`, `usar_apify` (bool), `canales` (subconjunto de tiktok/youtube/facebook) | Crea la tarjeta de confirmación |
| `consultar_estado` | `run_id` (opcional; si falta, el último run) | Lee el run y responde tareas y estados |
| `listar_pendientes` | `max` (1–100) | Videos generados que nunca llegaron al inbox |
| `estado_videos` | `desde` (fecha opcional), `canal` (opcional) | Qué se subió y qué no, por canal |
| `no_entendido` | `motivo` | Pide aclaración; no ejecuta nada |

**Toda cifra que el usuario no mencione queda en `null`**, no en un valor
inventado: el bot no adivina. Un valor fuera de rango invalida la interpretación
completa y se pide repetición, en lugar de recortarlo en silencio.

Si la respuesta no valida contra el esquema, se reintenta una vez; si vuelve a
fallar, se responde `no_entendido`.

**Sobre `min_likes`.** Es un filtro de selección de fuente que existe en el job
(`min_likes` sobre las filas de Apify), no una promesa de que el video vaya a
tener esos likes. La tarjeta lo muestra como *«solo fuentes con más de N likes»*
para que nadie lo lea como un resultado esperado.

### `acciones.py` — lo que de verdad toca el sistema

**`lanzar_lote`.** No crea jobs. Toma la definición viva de
`tiktok_lote_manual_10` (id `638404369584915`), copia sus parámetros por defecto y
sobrescribe **solo** los que la orden mencionó, con `jobs.run_now`. Los canales
se traducen a `disabled: true` en las tareas `publicar_youtube` y
`publicar_facebook` de esa corrida, no a cambios en el job.

Validaciones antes de mostrar la tarjeta: `cantidad` entero en rango, `min_likes`
entero no negativo, `hashtags` sin `#` y sin espacios (se normalizan), y al menos
un canal. Cualquier fallo se explica en el mensaje y no se ofrece el botón.

**`listar_pendientes`.** `gold.guiones_usados` menos `bronze.tiktok_uploads`, con
los nombres normalizados — la misma regla de `_v2` que usa
`pendientes_borradores`, para no reportar como pendiente algo que sí se subió con
sufijo.

**`estado_videos`.** La pregunta que motivó esta acción merece cuidado, porque
la respuesta no sale de una sola tabla:

- `bronze.tiktok_uploads` es la fuente real de TikTok. Estados que cuentan como
  subido: `SEND_TO_USER_INBOX` y `PUBLISH_COMPLETE`. `SKIPPED_INBOX_FULL` es
  *generado y no subido*: se reintenta solo en la corrida siguiente, así que se
  reporta como pendiente, no como fallo.
- `bronze.distribucion_video` es el registro por canal de YouTube y Facebook,
  pero **solo tiene filas desde que se creó** (7 filas al escribir esto), y
  **no contiene filas de TikTok**.

Por eso la respuesta se presenta en tres bloques, sin inventar un estado único:

1. **En el inbox de TikTok** — los que sí llegaron.
2. **Pendientes de TikTok** — generados y no subidos, con el motivo.
3. **Por canal (YouTube/Facebook)** — solo desde la fecha en que existe
   `distribucion_video`; antes de esa fecha se dice «sin registro», nunca
   «no publicado».

Se consulta con el SQL warehouse por defecto del workspace.

### `slack.py` — los mensajes

- **Tarjeta de confirmación**: qué job, y una tabla de parámetros con los valores
  entendidos. Los que no se mencionaron aparecen como «por defecto del job».
  Botones «Ejecutar» y «Cancelar».
- **Resultado**: estado del run, tareas fallidas si las hay, y `run_page_url`.
- **Respuestas de lectura**: listas cortas en texto; si superan 15 elementos, se
  agrupan y se ofrece el detalle completo bajo demanda.

## Datos y secretos

Claves nuevas en el scope `tiktok_pipeline`:

| Clave | Qué es |
|---|---|
| `slack_bot_token` | `xoxb-…` para responder en el canal |
| `slack_signing_secret` | Verifica que la petición viene de Slack |

Se reutilizan `openrouter_api_key` y el `slack_webhook_url` existente. Ningún
token se imprime en logs ni aparece en mensajes; viajan en cabeceras
`Authorization`, nunca en URLs.

**Permisos de la app de Slack:** `chat:write`, `commands` (opcional) y el scope
de eventos `app_mention` o `message.channels`.

## Manejo de errores

| Situación | Respuesta |
|---|---|
| Firma inválida | HTTP 401, sin detalle |
| Reintento de Slack | HTTP 200, ignorado |
| LLM caído o esquema inválido | «No pude interpretar la orden»; no se lanza nada |
| Parámetro fuera de rango | Se explica cuál y cuál es el rango; no hay botón |
| Job rechaza el run | El mensaje de la API tal cual, sin reintentar |
| Botón pulsado dos veces | El `value` firmado lleva un identificador de uso único; el segundo clic dice «ya se ejecutó» |
| Run falla después | El webhook de notificación que ya existe en el job sigue avisando |

## Pruebas

- **Unitarias, sin red:** `interpretar` con respuestas de LLM simuladas (válidas,
  fuera de esquema, fuera de rango); `acciones` con un cliente de Jobs falso;
  verificación de firma aceptando y rechazando.
- **De integración, con red:** un mensaje real en un canal de pruebas que se
  interpreta y produce la tarjeta, sin pulsar Ejecutar.
- **Manual de extremo a extremo:** una orden real que lanza un lote de 1 video
  con `max_pendientes=0`, y se confirma que llega a TikTok y a YouTube.

## Riesgos

**El LLM confunde «100 000 likes» con «100».** Se mitiga con tipos enteros,
rango declarado y la tarjeta de confirmación, donde el número se ve antes de
ejecutar.

**La app necesita estar viva.** Si Databricks reinicia la app o se queda sin
cómputo, las órdenes no llegan. El estado se consulta con `databricks apps get`.

**Permisos de Slack.** Crear la app en api.slack.com e instalar los secretos no
lo puede hacer el código: es un paso tuyo, documentado en el plan.

**Cupo de TikTok.** El bot no lo gestiona: el job ya recorta a
`max_pendientes` y marca `SKIPPED_INBOX_FULL`. Lanzar lotes seguidos gasta el
cupo de 5 subidas/24 h igual que hoy.

## Criterios de aceptación

1. Un mensaje «lanza 3 videos con #motivacion y min_likes 100000» produce una
   tarjeta con `cantidad=3`, `hashtags=[motivacion]`, `min_likes=100000` y el
   resto marcado como predeterminado.
2. Pulsar «Ejecutar» lanza el run y devuelve su enlace.
3. Pulsar «Cancelar» no lanza nada.
4. «¿Qué videos se subieron y cuáles no?» responde los tres bloques, y los videos
   de la raíz del volumen y de las carpetas por fecha se reportan igual.
5. «Súbelos a Instagram» responde que ese canal no existe todavía; no lanza nada.
6. Una petición sin firma válida devuelve 401 y no altera ningún estado.
