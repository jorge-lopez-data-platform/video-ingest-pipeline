# Arquitectura

Documento de diseño del pipeline de ingesta. Explica el flujo, las decisiones que
lo sostienen y qué ocurre cuando algo falla.

- [Vista general](#vista-general)
- [Componentes](#componentes)
- [Flujo de datos](#flujo-de-datos)
- [Modelo de datos](#modelo-de-datos)
- [Decisiones de diseño](#decisiones-de-diseño)
- [Idempotencia y recuperación](#idempotencia-y-recuperación)
- [Coste](#coste)
- [Modos de fallo](#modos-de-fallo)
- [Extensiones previstas](#extensiones-previstas)

## Vista general

```
┌──────────────────────────────────────────────────────────────────────┐
│  Databricks (serverless)                                             │
│                                                                      │
│  Job: tiktok_ingest_daily  (584295967848266, cron 06:00 UTC, PAUSED) │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ tarea única: ingest_tiktok                                     │  │
│  │   1. run del actor Apify   → polling hasta estado terminal     │  │
│  │   2. dataset paginado      → lista de dicts                    │  │
│  │   3. parseo + validación   → DataFrame tipado                  │  │
│  │   4. append a bronze       → snapshots                         │  │
│  │   5. rebuild silver + gold → CREATE OR REPLACE                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  Job: tiktok_download_own_video (349008211136613)                    │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ ingest_tiktok (run_job_task) → download_own_video              │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  Unity Catalog: catálogo tiktok                                      │
│    bronze.raw_videos · silver.videos · gold.creator_kpis             │
│    gold.viral_candidates · volume default.videos                     │
│  Secret scope: tiktok_pipeline/apify_token                           │
└──────────────────────────────────────────────────────────────────────┘
                        ▲
                        │ HTTPS  (Authorization: Bearer <token>)
                        │
              ┌─────────┴──────────┐
              │   API de Apify     │
              │  clockworks~tiktok-│
              │      scraper       │
              └────────────────────┘
```

## Componentes

| Componente | Responsabilidad | Dónde |
|---|---|---|
| `01_ingest_tiktok.py` | orquesta Apify, parsea, escribe bronze, reconstruye silver y gold | `notebooks/` |
| `02_download_own_video.py` | descarga videos de cuentas propias por `yt-dlp`, con allowlist | `notebooks/` |
| `jobs/ingest_daily.job.json` | definición del job diario | `jobs/` |
| `jobs/download_own_video.job.json` | job que encadena ingesta + descarga propia | `jobs/` |
| secret scope `tiktok_pipeline` | token de Apify | Databricks |
| volume `tiktok.default.videos` | almacenamiento de los mp4 | Unity Catalog |

## Flujo de datos

1. **Run del actor.** `POST /acts/{actor}/runs` con `hashtags`, `resultsPerPage`,
   `maxTotalChargeUsd` y `timeout`. La respuesta da `id` y `defaultDatasetId`.
2. **Polling.** Cada 10 s se consulta `GET /actor-runs/{id}`. Se sale cuando el
   estado es terminal (`SUCCEEDED`, `FAILED`, `ABORTED`, `TIMED-OUT`). Si se pasa
   del plazo, se pide `abort` y se lanza `TimeoutError`; no se deja un run huérfano
   consumiendo crédito.
3. **Descarga paginada.** `GET /datasets/{id}/items` en páginas de 500 hasta que
   una página vuelve vacía. Un dataset vacío es un error explícito, no un éxito
   silencioso.
4. **Parseo.** Se normalizan campos anidados (`authorMeta`, `videoMeta`,
   `musicMeta`) a columnas planas. Se descartan filas sin `id` o sin `author.id`
   y se cuentan como `skipped`.
5. **Escritura en bronze.** `append` con el esquema declarado y `ingested_at`
   añadido por Spark. El `apify_run_id` permite auditar de qué ejecución salió
   cada fila.
6. **Reconstrucción de silver y gold.** `CREATE OR REPLACE ... AS SELECT`, es
   decir, *full refresh* sobre bronze. No hay estado incremental que reconciliar.

## Modelo de datos

### bronze.raw_videos

Grano: **una observación por video y ejecución**. Es un log de hechos
inmutables, no una tabla de dimensión.

Columnas: `video_id`, `created_at`, `text_language`, `is_ad`, `is_sponsored`,
`has_shop_product`, `digg_count`, `share_count`, `play_count`, `collect_count`,
`comment_count`, `repost_count`, `author_id`, `author_name`, `author_verified`,
`author_fans`, `author_hearts`, `author_videos`, `duration_sec`, `music_id`,
`video_url`, `source_hashtag`, `apify_run_id`, `ingested_at`.

### silver.videos

Grano: **una fila por `video_id`**, el último snapshot por `ingested_at`.

Métricas derivadas:

| Columna | Fórmula |
|---|---|
| `total_interactions` | `digg + share + comment + collect` |
| `engagement_rate` | `total_interactions / play_count` |
| `share_rate` | `share_count / play_count` |
| `views_per_follower` | `play_count / author_fans` |
| `age_days` | `ingested_at - created_at` en días |

### gold.creator_kpis

Grano: **una fila por `author_id`**. Agrega silver y clasifica al creador en
`nano` (<1k), `micro` (<10k), `mid` (<100k) o `macro` (≥100k) seguidores.

### gold.viral_candidates

Solo en la variante curada. Filtra videos en español de ≤30 días y exige que
superen simultáneamente la mediana de `share_rate`, `engagement_rate` y
`views_per_follower` del lote («multiviral»). Ordena por `viral_score`, una
suma ponderada en escala logarítmica para que un único video con millones de
vistas no aplaste al resto.

## Decisiones de diseño

**Bronze es append-only.** Guardar cada observación permite medir cómo crecen las
métricas de un video en el tiempo y detectar republicaciones o cambios de
metadata. Sobrescribir en bronze destruiría esa señal.

**Silver y gold se reconstruyen enteros.** Con decenas de miles de filas, un
full refresh cuesta segundos y elimina toda una clase de bugs de MERGE (filas
duplicadas, borrados lógicos, orden de llegada). Si el volumen crece dos órdenes
de magnitud, habrá que pasar a incremental por `ingested_at`.

**El token nunca es un parámetro.** Se lee de `dbutils.secrets` dentro del
notebook. Un parámetro de job queda visible en la UI y en los logs.

**El parseo falla ruidosamente.** Sin filas válidas o con dataset vacío, el
notebook lanza excepción. Un job en verde con cero datos es peor que un fallo.

**El job nace en pausa.** El actor es de pago por uso; activar el cron es una
decisión explícita de coste, no un efecto colateral del despliegue.

## Idempotencia y recuperación

- **Reejecutar la ingesta** no corrompe nada: añade otra tanda de observaciones y
  silver se queda con la más reciente por `video_id`. El coste es de Apify, no de
  consistencia.
- **Reejecutar el modelado** es seguro: `CREATE OR REPLACE` parte siempre de
  bronze.
- **Un run de Apify abortado** no escribe nada porque la escritura ocurre después
  de la comprobación de estado.
- **Un fallo de red a mitad de la paginación** descarta el lote entero: no hay
  escritura parcial.

## Coste

El gasto lo determina el actor, facturado por resultados. El job fija
`maxTotalChargeUsd` (`max_charge_usd`: 1.0 en el notebook, 2.0 en el job
desplegado) y `results_per_hashtag` (50 en el notebook, 100 en el job).

La combinación desplegada —5 hashtags × 100 resultados × tope de 2 USD por
ejecución— con cron diario puede agotar el saldo del plan gratuito de Apify en
pocas ejecuciones. Antes de activar el cron, fija el presupuesto mensual y ajústalo
en `jobs/ingest_daily.job.json`.

Para desarrollo, usa una sola ejecución manual con hashtags de baja popularidad y
`max_charge_usd=0.5`.

## Modos de fallo

| Síntoma | Causa | Respuesta |
|---|---|---|
| `401 / 403` de Apify | token caducado o revocado | renovar el secret `apify_token` |
| `RuntimeError: Apify run ... ended with status FAILED` | el actor cambió de esquema o la cuenta no tiene saldo | ver `statusMessage` en el log |
| `TimeoutError ... abort requested` | el run tardó más que `run_timeout_secs` | subir el plazo o reducir resultados |
| `No valid rows after parsing` | cambió el esquema del actor | revisar `authorMeta`/`videoMeta` |
| `AnalysisException: table not found` | falta crear catálogo/esquemas | ver `docs/SETUP.md` |

Detalle operativo en `docs/TROUBLESHOOTING.md`.

## Extensiones previstas

- **Incrementalidad** en silver/gold cuando bronze pase de ~10⁶ filas.
- **Particionado** de bronze por fecha de ingesta (`ingested_at`) para recortar
  el coste de los full refresh.
- **Otras plataformas.** El diseño (bronze append-only + silver deduplicado +
  gold por entidad) se traslada a YouTube Shorts o Instagram Reels cambiando
  solo el extractor de la primera capa.
- **Alertas.** Los jobs admiten `webhook_notifications` a Slack; conviene añadirlo
  cuando el cron esté activo.
