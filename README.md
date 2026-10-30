# video-ingest-pipeline

Pipelines de Databricks para la cuenta propia de TikTok. El repositorio tiene dos partes
que comparten workspace, catálogo y secret scope:

- **Parte A: ingesta de métricas públicas.** Descarga datos de TikTok con Apify y los
  modela en capas bronze → silver → gold en Unity Catalog. Es el núcleo original del repo.
- **Parte B: producción diaria de contenido propio.** Genera un guion de 60 s, renderiza
  el video con voz y subtítulos, sube un borrador a TikTok y publica en YouTube y
  Facebook.

Todo el contenido que se procesa es **propio**. Ver [Aviso legal](#aviso-legal).

## Índice

- [Estado actual](#estado-actual)
- [Jobs del workspace](#jobs-del-workspace)
- [Parte A: ingesta de métricas](#parte-a-ingesta-de-métricas)
- [Parte B: producción diaria](#parte-b-producción-diaria)
- [Estructura del repositorio](#estructura-del-repositorio)
- [Requisitos y secretos](#requisitos-y-secretos)
- [Puesta en marcha](#puesta-en-marcha)
- [Operación](#operación)
- [Desarrollo y tests](#desarrollo-y-tests)
- [Seguridad](#seguridad)
- [Aviso legal](#aviso-legal)

Documentación detallada: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/SETUP.md](docs/SETUP.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md),
[docs/operacion-multicanal.md](docs/operacion-multicanal.md) (cadencia, tokens y
problemas conocidos de la publicación).

## Estado actual

Revisado el 2026-10-29.

- **Job en producción:** `tiktok_produccion_diaria` (813880207704409), activo
  (`UNPAUSED`). Un único disparador: cron `0 0 6,15,23 * * ?` en `America/Merida`.
  Cada ejecución consume crédito de Apify y de LLM.
- **Notebooks de producción:** `notebooks/produccion/` contiene los 8 notebooks que
  ejecuta el job, exportados del workspace. Los imports ya apuntan al layout de
  subpaquetes del repo, que **todavía no está desplegado** en el volumen (ver
  [Layout de imports](#layout-de-imports)).
- **Job de ingesta:** `tiktok_ingest_daily` (584295967848266), en pausa.
- **Tests:** `pytest` pasa en un entorno limpio con `requirements-dev.txt`
  (164 pasan y 28 se saltan por falta de pyspark). Los tests de `slackbot` no se
  ejecutan aquí porque ese paquete vive en el repo `tiktok-pipeline`.
- **Identificadores:** el email del workspace y el ID del destino de Slack se retiraron
  del árbol. Ver [Seguridad](#seguridad).

## Jobs del workspace

| Job | ID | Versionado en | Estado | Qué hace |
|---|---|---|---|---|
| `tiktok_produccion_diaria` | 813880207704409 | `jobs/tiktok_produccion_diaria.job.json` | activo | Parte B completa |
| `tiktok_ingest_daily` | 584295967848266 | `jobs/ingest_daily.job.json` | en pausa | Parte A: métricas de Apify |
| `tiktok_download_own_video` | 349008211136613 | `jobs/download_own_video.job.json` | — | Descarga de videos propios |
| `tiktok_lote_manual_10` | 638404369584915 | no versionado | etiquetado `production` | Lote manual de 10 videos |
| `tiktok_guiones_60s_viral` | 646167974890545 | `jobs/restore_20261024/` | — | Guiones virales de 60 s |

Hay además dos jobs sin documentar en el workspace (`vigilante_desatascos` y
`prueba_replica`). Ninguno está en el repo.

Los `jobs/*.job.json` usan el formato de `databricks jobs reset`: `{"job_id", "new_settings"}`.
Los placeholders se resuelven al desplegar, como se explica en
[Puesta en marcha](#puesta-en-marcha).

## Parte A: ingesta de métricas

Un job diario lanza el actor `clockworks~tiktok-scraper` de Apify sobre un conjunto de
hashtags, descarga el dataset paginado y lo escribe en `tiktok.bronze.raw_videos`. Cada
ejecución añade un **snapshot**, de modo que se puede medir cómo crecen las métricas de
un video con el tiempo y no solo su estado final.

1. **Ingesta** (`notebooks/01_ingest_tiktok.py`). Espera a que el run termine, descarga el
   dataset y lo añade a bronze. Cada fila tiene su propio `ingested_at`: ingerir dos veces
   el mismo video produce dos observaciones, no un duplicado lógico.
2. **Modelado** (misma tarea, al final). Reconstruye `tiktok.silver.videos` (último snapshot
   por video, con métricas derivadas) y `tiktok.gold.creator_kpis` (KPIs por creador con su
   tier de audiencia). La variante curada añade `tiktok.gold.viral_candidates`.
3. **Descarga de videos propios** (`notebooks/02_download_own_video.py`). Descarga con
   `yt-dlp` solo los videos cuyo autor esté en `own_handles`. Cualquier otro se omite con
   estado `skipped` antes de iniciar la descarga.

```
   Apify: clockworks~tiktok-scraper
              │  run (hashtags × resultsPerPage, tope de gasto)
              ▼
   01_ingest_tiktok ──► tiktok.bronze.raw_videos      (histórico de observaciones)
                              │ dedupe por video_id
                              ▼
                        tiktok.silver.videos          (último snapshot + métricas)
                              │ GROUP BY author_id
                              ▼
                        tiktok.gold.creator_kpis      (KPIs por creador + tier)
                              │
                        tiktok.gold.viral_candidates  (variante curada)
```

| Tabla | Grano | Contenido |
|---|---|---|
| `tiktok.bronze.raw_videos` | video × ejecución | métricas del actor, `apify_run_id`, `ingested_at` |
| `tiktok.silver.videos` | `video_id` | último snapshot, `total_interactions`, `engagement_rate`, `share_rate`, `views_per_follower`, `age_days` |
| `tiktok.gold.creator_kpis` | `author_id` | seguidores, videos muestreados, vistas, tasas, `creator_tier` |
| `tiktok.gold.viral_candidates` | ≤ 50 filas | candidatos en español, ≤ 30 días, virales en las tres dimensiones, con `viral_score` |

Las métricas son públicas: sirven para medir alcance y engagement, no para monetización.
El diseño prioriza que un fallo sea visible. Un dataset vacío o un cambio de esquema del
actor lanza excepción en lugar de dejar el job en verde con cero datos.

## Parte B: producción diaria

`tiktok_produccion_diaria` produce `cantidad` videos por ejecución (3 por defecto) a partir
de guiones virales, y los distribuye así:

```
guiones_60s ──► generar_video ──┬──► subir_borradores ──────────────────────┐
                                ├──► publicar_youtube ──────────────────────┤
                                └──► publicar_facebook ─────────────────────┤
                                                                            ▼
                                                                    cerrar_distribucion
```

| Tarea | Función |
|---|---|
| `guiones_60s` | selecciona temas y genera guiones de 60 s (`scripts/render/guiones_60s_viral.py`) |
| `generar_video` | voz con edge-tts, subtítulos, b-roll y personaje (`scripts/render/generar_video_guion.py`) |
| `subir_borradores` | notebook `subir_cola`: revisa los pendientes y llama a `upload_draft_video` para subir cada video al inbox de TikTok como borrador, respetando el cupo |
| `publicar_youtube`, `publicar_facebook` | publican cada video (`notebooks/produccion/publicar_*.py`) |
| `cerrar_distribucion` | cierra la ejecución, avisa a Slack y deja el registro de publicación |

**Publicación en TikTok.** La publicación directa está deshabilitada porque la app no está
auditada por TikTok: el pipeline solo sube borradores. La publicación final la hace una
persona desde la app de TikTok.

**Parámetros principales** (valores por defecto del job):

| Parámetro | Valor | Para qué sirve |
|---|---|---|
| `cantidad` | 2 | videos por ejecución |
| `guiones_objetivo` | 5 | guiones a generar por corrida |
| `max_pendientes` | 2 | tope de borradores pendientes en TikTok |
| `max_charge_usd` | 0.5 | tope de gasto del actor de Apify |
| `usar_apify` | true | obtener temas de tendencia con Apify |
| `llm_model` | claude-haiku-5-5 | modelo para guiones y personaje |
| `voz` | es-MX-JorgeNeural | voz de edge-tts |
| `personaje` | motivacional | personaje dibujado de la pieza |
| `hashtags` | lista de temas | hashtags de búsqueda |
| `allow_resubmit` | true | permite volver a subir un video ya procesado |

El job completo está en `jobs/tiktok_produccion_diaria.job.json`. Se versiona sin las
alertas de Slack del workspace, que hay que volver a añadir al desplegar.

## Estructura del repositorio

```
video-ingest-pipeline/
├── notebooks/                 notebooks de Databricks
│   ├── 01_ingest_tiktok.py        Parte A: Apify → bronze → silver → gold
│   ├── 02_download_own_video.py   Parte A: descarga solo los videos propios
│   └── produccion/                Parte B: los 8 notebooks del job tiktok_produccion_diaria
├── ingest_lib/                Código puro, sin Spark, testeable
│   └── parse.py                   normalización de items del actor
├── scripts/                   Parte B: scripts que ejecutan las tareas del job
│   ├── ingest/                    descripciones y descarga de videos propios
│   ├── render/                    guiones, video, render por lotes y export a Remotion
│   ├── upload/                    subida de borradores, publicación y registro
│   ├── util/                      utilidades: subtítulos, TTS, marca, personaje, cuota
│   ├── generate_original_video.sh video vertical original con ffmpeg
│   └── render_job_json.py         resuelve placeholders de jobs/ a build/jobs/
├── jobs/                      Definiciones de job (*.job.json, *.reset.json) y restore_20261024/
├── docs/                      Arquitectura, configuración, operación y diseños
├── assets/                    imágenes de marca y fondos
├── tests/                     tests de parseo, renderizado y utilidades
├── .github/workflows/ci.yml   compila notebooks y scripts y corre tests en cada push
├── pyproject.toml             configuración de pytest
└── requirements-dev.txt       requests + pytest para desarrollo local
```

La fuente de verdad de cada job es su `*.job.json` o el job vivo en el workspace.
`jobs/restore_20261024/` contiene la copia de restauración validada de los jobs.

## Requisitos y secretos

**Databricks**

- Workspace con Unity Catalog, catálogo `tiktok` y esquemas `bronze`, `silver` y `gold`.
- Volume `tiktok.default.videos`.
- CLI de Databricks `>= 1.0`, con un perfil autenticado.
- Serverless para los jobs de la Parte B.

**Secret scope `tiktok_pipeline`**

Los nombres de clave que leen los scripts. Los valores nunca van en el repo.

| Clave | Usada por |
|---|---|
| `apify_token` | ingesta y temas de tendencia (Apify) |
| `openrouter_api_key` | LLM para guiones y personaje |
| `tiktok_access_token` | subida de borradores a TikTok |
| `slack_webhook_url` | avisos de ejecución a Slack |

Los scripts de publicación en YouTube y Facebook leen además sus propias claves con
`secrets.get(SCOPE, key)`. Los nombres exactos están en el código (`grep -rn "secrets.get"`).

**Cuentas**

- Apify, con saldo. El actor es de pago por uso y el job fija un tope con `max_charge_usd`.
- TikTok, YouTube y Facebook, con las apps y tokens que indica
  [docs/TOKEN_VIDEO_PUBLISH.md](docs/TOKEN_VIDEO_PUBLISH.md).

## Puesta en marcha

Los pasos completos están en [docs/SETUP.md](docs/SETUP.md). El resumen:

```bash
# 1. Perfil de la CLI (usa siempre --profile; nunca el perfil por defecto)
databricks auth login --host https://<workspace> --profile <PERFIL>

# 2. Catálogo y esquemas
databricks experimental aitools tools query \
  "CREATE CATALOG IF NOT EXISTS tiktok" --profile <PERFIL>

# 3. Secret scope. Pasa el valor con --string-value desde tu propio shell;
#    nunca lo escribas en archivos del repo
databricks secrets create-scope tiktok_pipeline --profile <PERFIL>
databricks secrets put-secret tiktok_pipeline apify_token --string-value "$APIFY_TOKEN" --profile <PERFIL>

# 4. Notebooks al workspace
databricks workspace import-dir notebooks /tiktok_ingest --profile <PERFIL>

# 5. Renderizar placeholders y crear o restaurar un job
python scripts/render_job_json.py --workspace-user <usuario-del-workspace> --out-dir build/jobs
databricks jobs reset --json @build/jobs/tiktok_produccion_diaria.job.json --profile <PERFIL>
```

`build/` está en `.gitignore` porque el render sustituye los placeholders por datos reales.

## Operación

```bash
databricks jobs list --profile <PERFIL>
databricks jobs run-now --job-id 813880207704409 --profile <PERFIL>
databricks jobs get-run-output <TASK_RUN_ID> --profile <PERFIL>
```

- **Pausar o despausar:** cambia `pause_status` en el job y vuelve a aplicar el `reset`.
  El job de producción está activo: cada ejecución consume crédito de Apify y LLM y publica
  en YouTube y Facebook.
- **Ingesta de métricas:** `tiktok_ingest_daily` viene en pausa (`0 0 6 * * ?` UTC). Actívalo
  cuando el coste de Apify esté controlado.
- **Disparador:** el job tiene un único cron (06:00, 15:00 y 23:00 hora de Mérida). Un
  intervalo de 4 horas que existía en la versión anterior ya no está en el job vivo.

## Layout de imports

- El repo usa subpaquetes: `scripts/util`, `scripts/render`, `scripts/upload` y
  `scripts/ingest`. Los notebooks de `notebooks/produccion/` importan con esa estructura,
  por ejemplo `from scripts.util import rutas`.
- El volumen `/Volumes/tiktok/default/videos/lib` tiene un **layout plano**
  (`scripts/rutas.py`, `scripts/common.py`…) y se carga con `sys.path`. Hasta desplegar el
  layout del repo en ese volumen, los notebooks del repo no corren contra producción.
- Hay divergencia de lógica: `personaje_llm.py`, `titulos.py`, `seleccion.py` y
  `jesucitos.py` del repo son anteriores a los del volumen, que usan Claude. **El volumen
  es la referencia de producción** hasta que se sincronice el repo.
- `scripts/util/llm.py` es idéntico al del volumen y lo necesitan los notebooks.

## Desarrollo y tests

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements-dev.txt
pytest
```

> **Estado:** en un venv limpio, `pytest` da 164 pasados y 28 saltados. Los saltados
> son los tests de `registrar_publicacion` que necesitan pyspark. Los tests de `slackbot`
> se excluyen si el paquete no está disponible (`tests/conftest.py`); viven en el repo
> `tiktok-pipeline`.
>
> `tests/test_syntax.py` compila todos los `.py` del pipeline. Los notebooks exportados
> usan `await` de nivel superior, así que se compilan con
> `PyCF_ALLOW_TOP_LEVEL_AWAIT`.

Los notebooks usan `dbutils` y `spark`, así que no corren en local. Los tests cubren las
piezas puras: parseo, normalización de items, renderizado de payloads, cuota y utilidades
de video.

## Seguridad

- **Sin credenciales en el código.** Un escaneo de patrones (tokens de Slack, GitHub,
  OpenAI/OpenRouter, AWS, JWT y asignaciones de `token`, `secret` o `password` con valor
  literal) no encontró credenciales reales. El único valor que coincide es un valor falso
  de test.
- **Secretos en el secret scope.** Los scripts leen sus claves del secret scope
  `tiktok_pipeline`. `.gitignore` excluye `.env`, `.databricks/`, `build/` y los videos.
- **Identificadores personales.** El email del workspace se sustituyó por `USUARIO` en
  todos los archivos. El ID del destino de Slack se sustituyó por
  `<SLACK_DESTINATION_ID>` en código y documentación, y los bloques
  `webhook_notifications` se quitaron de los JSON. Los scripts que aplican avisos de
  Slack necesitan ese ID como parámetro.
- **Historial.** Los valores anteriores siguen en commits antiguos. Eliminarlos del
  historial exige reescribirlo; no se ha hecho.
- **Jobs con placeholders.** Los `*.job.json` usan `USUARIO` y `tu_cuenta`; el usuario real
  solo aparece en el `build/` local.

## Aviso legal

Este repositorio procesa **métricas públicas** y publica **solo contenido propio**.

Descargar y republicar videos de otros creadores infringe sus derechos de autor y las
normas de TikTok, y puede acabar en la suspensión de la cuenta. Tampoco se debe eliminar la
metadata de un video para ocultar su origen. Por eso los notebooks de un flujo anterior
(`01_scrape_viral_videos`, `02_download_video` y `03_upload_to_tiktok`), que seleccionaban
el video con más plays de `#fyp` y lo volvían a publicar, no forman parte de este repo.
Están archivados fuera de Git y no deben reintroducirse.

Para producir contenido propio, `scripts/generate_original_video.sh` genera un video
vertical original con ffmpeg, y la Parte B genera guion, voz y animación propios.
