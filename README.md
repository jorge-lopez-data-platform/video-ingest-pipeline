# video-ingest-pipeline — Ingesta de métricas de TikTok en Databricks

Ingesta diaria de métricas públicas de TikTok (vistas, likes, shares, comentarios,
datos del creador) desde la API de Apify hacia un lakehouse en Unity Catalog,
con modelado en capas bronze → silver → gold y un job de descarga de los videos
propios del usuario.

Convierte la actividad pública de TikTok en tablas analizables. Un job diario lanza
el actor `clockworks/tiktok-scraper` de Apify sobre un conjunto de hashtags,
descarga el dataset paginado y lo escribe en `tiktok.bronze.raw_videos` como un
histórico de observaciones: cada ejecución añade un snapshot, de modo que se puede
medir cómo crecen las métricas de un video con el tiempo, no solo su estado final.

Sobre ese histórico se reconstruyen `tiktok.silver.videos` (último snapshot por
video, con engagement rate, share rate y views-per-follower) y
`tiktok.gold.creator_kpis` (KPIs por creador clasificados en tiers de audiencia).
Una variante curada añade `gold.viral_candidates`, que selecciona videos en español
de menos de 30 días que destacan a la vez en las tres dimensiones de viralidad, no
solo en vistas.

El diseño prioriza que un fallo sea visible: un dataset vacío o un cambio de
esquema del actor lanza excepción en lugar de dejar un job en verde con cero datos.

Todo el contenido que descarga este repo es **propio**. La parte que descargaba y
republicaba videos de otros creadores está archivada y fuera de este repositorio;
ver [Aviso legal](#aviso-legal).

## Índice

- [Qué hace](#qué-hace)
- [Arquitectura](#arquitectura)
- [Modelo de datos](#modelo-de-datos)
- [Estructura del repositorio](#estructura-del-repositorio)
- [Requisitos](#requisitos)
- [Puesta en marcha](#puesta-en-marcha)
- [Operación](#operación)
- [Desarrollo local](#desarrollo-local)
- [Aviso legal](#aviso-legal)

Documentación detallada: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/SETUP.md](docs/SETUP.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Qué hace

1. **Ingesta** (`notebooks/01_ingest_tiktok.py`). Lanza el actor
   `clockworks~tiktok-scraper` de Apify para una lista de hashtags, espera a que
   termine, descarga el dataset paginado y lo añade a `tiktok.bronze.raw_videos`.
   Cada fila es una *observación* con su propio `ingested_at`: ingerir dos veces el
   mismo video produce dos snapshots, no un duplicado lógico.

2. **Modelado** (misma tarea, al final). Reconstruye
   `tiktok.silver.videos` (último snapshot por `video_id`, con métricas derivadas)
   y `tiktok.gold.creator_kpis` (KPIs agregados por creador con su tier).
   La variante curada añade además `gold.viral_candidates`.

3. **Descarga de videos propios** (`notebooks/02_download_own_video.py`).
   Descarga por `yt-dlp` solo videos cuyo autor esté en `own_handles`; cualquier
   otro se omite con estado `skipped` antes de iniciar la descarga.

## Arquitectura

```
   Apify: clockworks~tiktok-scraper
              │  run (hashtags × resultsPerPage, tope de gasto)
              ▼
   ┌──────────────────────────────┐
   │ 01_ingest_tiktok             │   polling del run → dataset paginado
   └──────────────┬───────────────┘
                  ▼ append
        tiktok.bronze.raw_videos          (histórico de observaciones)
                  │ CREATE OR REPLACE (dedupe por video_id)
                  ▼
        tiktok.silver.videos              (último snapshot + métricas derivadas)
                  │ GROUP BY author_id
                  ▼
        tiktok.gold.creator_kpis          (KPIs por creador + tier)
                  │
        tiktok.gold.viral_candidates      (solo en la variante curada)
```

Detalle de tareas, idempotencia, coste y fallos en
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Modelo de datos

| Tabla | Grano | Qué contiene |
|---|---|---|
| `tiktok.bronze.raw_videos` | una fila por video y ejecución | todas las métricas del actor, `apify_run_id`, `ingested_at` |
| `tiktok.silver.videos` | una fila por `video_id` | último snapshot + `total_interactions`, `engagement_rate`, `share_rate`, `views_per_follower`, `age_days` |
| `tiktok.gold.creator_kpis` | una fila por `author_id` | seguidores, videos muestreados, vistas, tasas, `creator_tier` |
| `tiktok.gold.viral_candidates` | ≤ 50 filas | candidatos en español, ≤ 30 días, virales en las tres dimensiones, con `viral_score` |

No hay datos de ingresos reales: todas las métricas son públicas y sirven para
entender alcance y engagement, no monetización.

## Estructura del repositorio

```
video-ingest-pipeline/
├── notebooks/
│   ├── 01_ingest_tiktok.py         # Apify → bronze → silver → gold
│   └── 02_download_own_video.py    # descarga solo los videos propios
├── ingest_lib/
│   └── parse.py                    # normalización de items, sin Spark (testeable)
├── jobs/
│   ├── ingest_daily.job.json       # job tiktok_ingest_daily (584295967848266)
│   └── download_own_video.job.json # job tiktok_download_own_video (349008211136613)
├── docs/
│   ├── ARCHITECTURE.md             # diseño, decisiones, idempotencia y fallos
│   ├── SETUP.md                    # alta del workspace, secrets, volumen, jobs
│   └── TROUBLESHOOTING.md          # errores conocidos y solución
├── scripts/
│   ├── generate_original_video.sh  # video vertical original con ffmpeg
│   └── render_job_json.py          # resuelve placeholders de jobs/ a build/jobs/
├── tests/                          # parseo y renderizado de payloads
├── requirements-dev.txt            # requests + pytest para desarrollo local
└── .github/workflows/ci.yml        # compila notebooks y corre tests en cada push
```

Los JSON de `jobs/` son payloads válidos de `databricks jobs reset` (formato
`{"job_id", "new_settings"}`, sin campos de solo lectura). Contienen ids de job y
rutas del workspace, ningún secreto. Se versionan con los placeholders `USUARIO` y
`tu_cuenta`; `scripts/render_job_json.py` los resuelve a `build/jobs/` (ignorado
por Git) antes de aplicarlos.

## Requisitos

- Workspace de Databricks con Unity Catalog y el catálogo `tiktok`.
- Volume `tiktok.default.videos`.
- Secret scope `tiktok_pipeline` con `apify_token`.
- Cuenta de Apify con saldo. El actor es de pago por uso y el job fija un tope
  con `max_charge_usd`.

Pasos detallados: [docs/SETUP.md](docs/SETUP.md).

## Puesta en marcha

```bash
# 1. Configura el perfil de la CLI
databricks auth login --host https://<workspace> --profile <PERFIL>

# 2. Crea el catálogo y el volumen
databricks experimental aitools tools query \
  "CREATE CATALOG IF NOT EXISTS tiktok" --profile <PERFIL>

# 3. Sube los secrets (nunca van en el repo)
databricks secrets create-scope tiktok_pipeline --profile <PERFIL>
databricks secrets put-secret tiktok_pipeline apify_token --profile <PERFIL>

# 4. Importa los notebooks al workspace
databricks workspace import-dir notebooks /tiktok_ingest --profile <PERFIL>

# 5. Renderiza los placeholders y crea o restaura el job
python scripts/render_job_json.py --workspace-user <tu-correo> --out-dir build/jobs
databricks jobs reset --json @build/jobs/ingest_daily.job.json --profile <PERFIL>
```

## Operación

```bash
databricks jobs list --profile <PERFIL>
databricks jobs run-now --job-id 584295967848266 --profile <PERFIL>
databricks jobs get-run-output <TASK_RUN_ID> --profile <PERFIL>
```

El job diario viene **en pausa** (`pause_status: PAUSED`, cron `0 0 6 * * ?` en UTC).
Actívalo solo cuando el coste de Apify esté controlado: cada ejecución consume
crédito real.

## Desarrollo local

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements-dev.txt
pytest
```

Los notebooks usan `dbutils`, así que no se ejecutan tal cual en local; los tests
cubren las piezas puras (parseo, normalización, cálculo de métricas).

## Aviso legal

Este repositorio ingiere **métricas públicas** y descarga **solo videos propios**.

Descargar y republicar videos de otros creadores infringe sus derechos de autor y
las normas de TikTok, y puede acabar en la suspensión de la cuenta. Tampoco se debe
eliminar la metadata de un video para ocultar su origen. Por eso los notebooks
`01_scrape_viral_videos`, `02_download_video` y `03_upload_to_tiktok`, que
seleccionaban el video con más plays de `#fyp` y lo volvían a publicar, no forman
parte de este repo: están archivados fuera de Git y no deben reintroducirse.

Para producir contenido propio, `scripts/generate_original_video.sh` genera un
video vertical original con ffmpeg como punto de partida.

## Seguridad

- Ningún token vive en el repo: los secrets están en el secret scope de Databricks
  o en variables de entorno.
- `jobs/*.json` no contiene credenciales, solo configuración.
- `.gitignore` excluye `.env`, `*.mp4`, `.venv/` y `.databricks/`.
