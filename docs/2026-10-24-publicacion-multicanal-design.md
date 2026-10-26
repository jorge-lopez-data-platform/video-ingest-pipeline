# Publicación multicanal: TikTok, YouTube Shorts y Facebook Reels

Estado: diseño aprobado, pendiente de credenciales y despliegue.
Fecha: 2026-10-24.

## 1. Punto de partida (verificado en el workspace)

Job `813880207704409` (`tiktok_produccion_diaria`), **pausado**, cron `0 0 9 * * ?` UTC, `cantidad=3`.

```
guiones_60s  →  generar_video  →  subir_borradores (for_each)  →  listar_pendientes
```

- `generar_video` publica en `taskValues`: `videos` (lista de `{video_name}`), `video_name`, `hashtags`.
- Cada video deja un `.txt` hermano con título y hashtags en `/Volumes/tiktok/default/videos`.
- `subir_borradores` es un `for_each_task` sobre `tasks.generar_video.values.videos`.
- Entornos serverless: `video` (pillow, edge-tts, imageio-ffmpeg, openai) y `publish` (sin dependencias).
- En el repo **no hay nada de YouTube ni Facebook**: ni código, ni credenciales.

En el secret scope `tiktok_pipeline` hay: `apify_token`, `openrouter_api_key`, `slack_webhook_url`,
`tiktok_access_token`, `tiktok_client_key`, `tiktok_client_secret`, `tiktok_refresh_token`.
Faltan todas las de YouTube y Meta.

## 2. Decisión de cadencia: 6/día, no 24

La restricción real no es TikTok, es YouTube. La cuota de YouTube Data API v3 es de
**10 000 unidades/día** y cada `videos.insert` cuesta **1 600**. El techo es **6 videos/día**
(9 600 unidades). Un job horario produciría 24 y YouTube empezaría a devolver
`quotaExceeded` a las seis de la mañana.

Cadencia elegida: **6 videos/día**, cron `0 0 0,4,8,12,16,20 * * ?` UTC, `cantidad=1`.
Quedan 400 unidades de margen para reintentos y consultas de estado.

Las tres redes reciben **el mismo video**, no un subconjunto: simplifica el DAG y evita una
cola de selección que habría que mantener.

## 3. Dos auditorías pendientes, no una

Es el punto que más frena la automatización al 100% y conviene tenerlo claro antes de escribir código.

| Plataforma | Requisito | Efecto mientras no esté auditada |
|---|---|---|
| TikTok | Direct Post auditado | Solo permite dejar **borradores** en el inbox |
| YouTube | API audit for projects created after 2020-07-28 | Todo `videos.insert` queda **`private`**, aunque se pida `public` |

Es decir: aun con credenciales perfectas y código correcto, **ni TikTok ni YouTube podrán publicar
en público solos** hasta pasar sus auditorías. El flujo se construye completo y cada canal arranca
en su modo permitido:

- **TikTok**: borrador en el inbox + aviso por Slack (ya implementado y en producción).
- **YouTube**: sube en `private`, y una tarea aparte lo pasa a `public` cuando la auditoría esté
  concedida. Mientras tanto queda como borrador privado, igual que TikTok.
- **Facebook Reels**: **no tiene esta restricción**. Con `pages_manage_posts` publica en público
  directamente. Es el único canal que llega al 100% real desde el primer día.

Esta es la razón de fondo por la que el flujo se diseña con un estado por canal y no con un
«publicado / fallido» binario.

## 4. Tabla de distribución (bronce, una fila por video y canal)

`tiktok.bronze.distribucion_video`

| Columna | Tipo | Notas |
|---|---|---|
| `video_name` | STRING | clave junto con `canal` |
| `video_fuente_id` | STRING | enlace al guion de `gold.guiones_60s` |
| `canal` | STRING | `tiktok`, `youtube`, `facebook` |
| `estado` | STRING | `PENDIENTE`, `EN_CURSO`, `PUBLICADO`, `PUBLICADO_PRIVADO`, `SKIPPED_CUOTA`, `FALLIDO` |
| `id_externo` | STRING | `publish_id` / `videoId` / `reel_video_id` |
| `url` | STRING | enlace público si lo hay |
| `motivo_fallo` | STRING | código de error de la API |
| `run_id` | STRING | |
| `intentos` | INT | |
| `creado_en` | TIMESTAMP | |
| `actualizado_en` | TIMESTAMP | |

Grano natural `(video_name, canal)`. La escritura es `MERGE` sobre esa clave, nunca `INSERT` ciego:
cada reintento actualiza la fila en vez de duplicarla. Esto también hace idempotente el reintento
de un video que falló a medias.

`bronze.tiktok_uploads` se mantiene como está para no romper `pendientes_borradores`;
`distribucion_video` la resume para los tres canales.

## 5. DAG objetivo

```
                       ┌─ subir_borradores   (tiktok,   for_each) ─┐
guiones_60s → generar_video ─┼─ publicar_youtube   (youtube,  for_each) ─┼→ cerrar_distribucion
                       └─ publicar_facebook  (facebook, for_each) ─┘
```

- Las tres ramas cuelgan de `generar_video` con `run_if: ALL_DONE` para que un fallo de un canal
  no impida publicar en los otros dos.
- `cerrar_distribucion` corre con `ALL_DONE`: consolida en bronce y manda **un solo** mensaje de
  Slack con el resumen de los tres canales, en vez de tres avisos separados.
- `listar_pendientes` se mantiene y pasa a depender también de `publicar_youtube`, para reintentar
  lo que no llegó a TikTok.

## 6. Credenciales necesarias (secret scope `tiktok_pipeline`)

### YouTube
1. Google Cloud: proyecto, YouTube Data API v3 habilitada, pantalla de consentimiento OAuth.
2. Cliente OAuth de tipo «Desktop» → `client_id`, `client_secret`.
3. Autorización con scope `https://www.googleapis.com/auth/youtube.upload`.
4. Guardar `youtube_client_id`, `youtube_client_secret`, `youtube_refresh_token`.

El access token dura 1 h; se pide uno nuevo con el refresh token en cada corrida. El refresh token
solo caduca si se revoca o si la app sigue en modo «Testing» (entonces caduca a los 7 días) —
**hay que publicar la app en producción**, si no el flujo se rompe cada semana.

### Facebook
1. Meta for Developers: app tipo «Business».
2. Producto «Facebook Login» y permisos `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`.
3. Token de usuario de larga duración → `/me/accounts` → **page access token** (no caduca mientras
   la app siga publicada y el usuario no revoque).
4. Guardar `fb_page_id`, `fb_page_access_token`, `fb_graph_version` (p. ej. `v21.0`).

Solo se publica en una página propia; no se toca nada de terceros.

## 7. Detalle por canal

### TikTok (sin cambios, modo borrador)
Ya implementado. Se queda como está: `inbox/video/init/`, registro en bronce y aviso por Slack.
`spam_risk_too_many_pending_share` se registra como `SKIPPED_INBOX_FULL` y no se reintenta.

### YouTube Shorts
- `videos.insert` con `part=snippet,status`, `uploadType=resumable`.
- `snippet.title` = primer línea del `.txt` hermano, recortado a 100 caracteres.
- `snippet.description` = hashtags del `.txt` + `#Shorts`.
- `snippet.categoryId = 22` (People & Blogs), `defaultLanguage = es`.
- `status.privacyStatus` = `private` hasta que la auditoría esté concedida.
- `status.selfDeclaredMadeForKids = false`.
- Vertical y < 3 min: YouTube lo clasifica como Short automáticamente.
- Si la API devuelve `quotaExceeded`, el estado es `SKIPPED_CUOTA` y **no** se reintenta ese día.

### Facebook Reels
Tres pasos sobre `graph-video.facebook.com` / `graph.facebook.com`:
1. `POST /{page_id}/video_reels` con `upload_phase=start` → devuelve `video_id` y `upload_url`.
2. `POST {upload_url}` (host `rupload.facebook.com`) con el binario y cabeceras
   `offset`, `file_size`, `Authorization: OAuth <token>`.
3. `POST /{page_id}/video_reels` con `upload_phase=finish`, `video_id`,
   `video_state=PUBLISHED`, `description` con hashtags.
4. Sondeo de `GET /{video_id}?fields=status` hasta `processing: complete`.

Restricciones de Reels: 9:16, 3–90 s. El video es 1080×1920 y 60 s, así que encaja sin recodificar.
El binario se genera con ffmpeg desde cero en el pipeline, no se descarga de TikTok, así que no
arrastra metadatos de otra plataforma.

## 8. Idempotencia y reintentos

- Antes de llamar a cualquier API, cada tarea hace `SELECT estado FROM distribucion_video
  WHERE video_name=? AND canal=?` y sale si ya está `PUBLICADO` o `PUBLICADO_PRIVADO`.
- Así el `for_each` puede reintentarse entero sin duplicar publicaciones, y `max_retries` es seguro.
- Los errores de cuota (`quotaExceeded`) y los deterministas (`spam_risk_*`) **no** se reintentan:
  se marcan y se sigue. Solo los 5xx y los timeouts entran en reintento con backoff.

## 9. Fases de despliegue

| Fase | Qué hace | Requiere |
|---|---|---|
| 1 | Crear `distribucion_video`, backfill desde `tiktok_uploads` | nada |
| 2 | Activar `publicar_facebook` (único canal público real) | token de página Meta |
| 3 | Activar `publicar_youtube` en `private` | OAuth Google en producción |
| 4 | Cambiar YouTube a `public` | auditoría de YouTube concedida |
| 5 | Cambiar TikTok a Direct Post | auditoría de TikTok concedida |

Cada fase es independiente: el DAG ya soporta que un canal esté desactivado
(`disabled: true` en la tarea) sin tocar los demás.

## 10. Coste y límites operativos

- 6 corridas/día × 3 destinos = 18 llamadas de publicación diarias.
- YouTube: 9 600 de 10 000 unidades. Es el límite duro; subir de 6/día exige pedir ampliación de
  cuota a Google.
- Facebook: sin coste, límite de ~30 publicaciones/hora por página.
- TikTok: cupo de borradores pendientes en el inbox (el que ya nos bloqueó).
- Serverless: el entorno `publish` no necesita dependencias nuevas; todo va con `requests`.
