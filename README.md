# tiktok_viral_videos

Pipeline de Databricks que detecta videos virales de TikTok, los descarga y los publica en una cuenta mediante la Content Posting API. Incluye además un script independiente para publicar por API desde local y un generador de videos originales.

## Estructura

```
tiktok_viral_videos/
├── notebooks/
│   ├── 01_scrape_viral_videos.py   # Apify: busca por hashtag y elige el video con más plays
│   ├── 02_download_video.py        # Descarga el mp4 a un Volume de Unity Catalog
│   └── 03_upload_to_tiktok.py      # Direct Post a TikTok y polling del estado
├── scripts/
│   ├── direct_post.py              # Mismo flujo de subida, ejecutable desde local
│   └── generate_original_video.sh  # Genera un video vertical original con ffmpeg
└── docs/
    ├── SETUP.md                    # Configuración paso a paso
    └── TROUBLESHOOTING.md          # Errores conocidos y solución
```

## Arquitectura

Job de Databricks `tiktok_viral_pipeline` con 3 tareas encadenadas. Las tareas se pasan datos con `dbutils.jobs.taskValues`.

| Tarea | Notebook | Entradas | Salidas (taskValues) |
|---|---|---|---|
| `scrape_viral_videos` | `01_scrape_viral_videos` | secret `apify_token`, hashtags `fyp` y `viral` | `video_url`, `video_id`, `caption` (150 caracteres) |
| `download_video` | `02_download_video` | `video_url`, `video_id` | `local_video_path` (`/Volumes/tiktok/default/videos/<id>.mp4`) |
| `upload_to_tiktok` | `03_upload_to_tiktok` | `local_video_path`, `caption`, secret `tiktok_access_token` | `publish_id` y estado en el log |

```
Apify (clockworks~tiktok-scraper)
        │  50 resultados por hashtag, orden por playCount
        ▼
01 scrape ──► 02 download ──► 03 upload ──► TikTok Content Posting API
                  │                              (init → PUT → status)
                  ▼
      Volume tiktok.default.videos
```

Datos relacionados: la tabla `tiktok.bronze.raw_videos` guarda métricas de videos scrapeados (`video_id`, `author_name`, `play_count`, `digg_count`, `share_count`, `video_url`, `source_hashtag`, `apify_run_id`, `ingested_at`, entre otras). Los notebooks de este repo no la escriben, y sirve para analizar qué contenido funciona.

## Requisitos

- Workspace de Databricks con Unity Catalog y un Volume `tiktok.default.videos`.
- Secret scope `tiktok_pipeline` con las claves `apify_token` y `tiktok_access_token`.
- App en el TikTok Developer Portal con el producto **Content Posting API**.
- Token de usuario de TikTok con el scope `video.publish` (publicación directa) o `video.upload` (borrador).

Pasos detallados en [docs/SETUP.md](docs/SETUP.md).

## Uso

En Databricks, ejecuta el job `tiktok_viral_pipeline` desde Jobs & Pipelines.

Desde local, con un token ya autorizado:

```
set TIKTOK_ACCESS_TOKEN=act.xxxxx
python scripts/direct_post.py ruta/video.mp4 "Titulo del video #hashtag"
```

El script consulta `creator_info` y usa `PUBLIC_TO_EVERYONE` solo si la cuenta lo permite. Si no, publica como `SELF_ONLY`.

Para generar un video original: `bash scripts/generate_original_video.sh` (requiere ffmpeg).

## Limitaciones conocidas

- `scope_not_authorized` en el init: el token no tiene `video.publish`. Ver [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
- Mientras la app de TikTok no esté auditada, los posts de Direct Post solo se permiten en modo privado (`SELF_ONLY`). El notebook 03 lo fuerza.
- El PUT sube el archivo en un único chunk. Para videos grandes (más de 64 MB) hay que dividirlo en chunks.

## Aviso legal y de uso

El notebook 01 selecciona videos públicos de otros creadores, y el 02 y el 03 los descargan y republican. Reutilizar contenido ajeno sin permiso infringe los derechos de autor del creador y las normas de TikTok, y puede acabar en la suspensión de la cuenta. Tampoco se debe eliminar la metadata de un video para ocultar su origen.

Usa este pipeline únicamente con contenido propio o con licencia, o con permiso explícito. `scripts/generate_original_video.sh` sirve de punto de partida para producir contenido original.

## Seguridad

- Ningún token se guarda en el repo. Los tokens viven en el secret scope de Databricks o en variables de entorno.
- No subas archivos `.env`, tokens ni videos descargados (ver `.gitignore`).
