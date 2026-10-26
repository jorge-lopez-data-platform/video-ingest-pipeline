# Configuración

Puesta en marcha desde cero. Ordena los pasos como los necesita el job, no como
los agrupa mentalmente Databricks.

1. [CLI y perfil](#1-cli-y-perfil)
2. [Catálogo, esquemas y volumen](#2-catálogo-esquemas-y-volumen)
3. [Secret scope](#3-secret-scope)
4. [Importar los notebooks](#4-importar-los-notebooks)
5. [Crear los jobs](#5-crear-los-jobs)
6. [Primera ejecución](#6-primera-ejecución)

## 1. CLI y perfil

```bash
databricks version          # debe ser >= 1.0
databricks auth login --host https://<workspace>.cloud.databricks.com --profile <PERFIL>
databricks auth profiles
```

Usa siempre `--profile`. Sin él, la CLI toma el perfil marcado por defecto y es
fácil lanzar algo contra el workspace equivocado.

## 2. Catálogo, esquemas y volumen

El notebook asume que el catálogo y los esquemas existen; no los crea.

```bash
databricks experimental aitools tools query \
  "CREATE CATALOG IF NOT EXISTS tiktok" --profile <PERFIL>

databricks experimental aitools tools query \
  "CREATE SCHEMA IF NOT EXISTS tiktok.bronze" --profile <PERFIL>

databricks experimental aitools tools query \
  "CREATE SCHEMA IF NOT EXISTS tiktok.silver" --profile <PERFIL>

databricks experimental aitools tools query \
  "CREATE SCHEMA IF NOT EXISTS tiktok.gold" --profile <PERFIL>

databricks experimental aitools tools query \
  "CREATE VOLUME IF NOT EXISTS tiktok.default.videos" --profile <PERFIL>
```

## 3. Secret scope

```bash
databricks secrets create-scope tiktok_pipeline --profile <PERFIL>
databricks secrets put-secret tiktok_pipeline apify_token --profile <PERFIL>
```

`put-secret` abre un editor interactivo; pega el token y guarda. Nunca lo pases
como parámetro de job ni lo escribas en un archivo del repo.

Comprueba que quedó bien sin imprimir el valor:

```bash
databricks secrets list-secrets tiktok_pipeline --profile <PERFIL>
```

## 4. Importar los notebooks

```bash
databricks workspace import-dir notebooks /Users/<tu-correo>/tiktok_ingest --profile <PERFIL>
databricks workspace import-dir ingest_lib /Users/<tu-correo>/tiktok_ingest/ingest_lib --profile <PERFIL>
```

La segunda orden es obligatoria: el notebook `01_ingest_tiktok.py` añade su propia
carpeta al `sys.path` e importa `ingest_lib.parse` desde la carpeta hermana. Si
falta, el run muere con `ModuleNotFoundError: No module named 'ingest_lib'`.

La ruta debe coincidir con el `notebook_path` de los payloads de `jobs/`
(`/Workspace/Users/<usuario>/tiktok_ingest/...`). Si importas en otra ruta, edita
los JSON antes de renderizarlos, o los jobs fallarán al no encontrar el notebook.

Los jobs de `jobs/` traen placeholders (`USUARIO`, `tu_cuenta`) para no versionar
identificadores personales. Renderízalos a `build/jobs/` antes de aplicarlos; ese
directorio está en `.gitignore`, así que tus datos no se publican:

```bash
python scripts/render_job_json.py \
  --workspace-user <tu-correo-del-workspace> \
  --own-handles <tus-cuentas,separadas,por,coma> \
  --out-dir build/jobs
```

Si no los renderizas, `jobs reset` falla por ruta inexistente.
Si importas los notebooks en otra ruta, edita el `notebook_path` del JSON antes de
renderizarlo.

## 5. Crear los jobs

Los archivos de `jobs/` están en el formato que acepta `jobs reset`: un objeto
con `job_id` y `new_settings`.

```bash
# Job diario de ingesta
databricks jobs reset --json @build/jobs/ingest_daily.job.json --profile <PERFIL>

# Job que encadena ingesta + descarga de videos propios
databricks jobs reset --json @build/jobs/download_own_video.job.json --profile <PERFIL>
```

Si los ids no existen en tu workspace, `reset` falla. En ese caso crea el job con
`databricks jobs create --json` usando solo el contenido de `new_settings`.

Dos campos son de solo lectura y **no** deben añadirse al JSON: `format`
(y `created_time`, `creator_user_name`, `run_as_user_name`). Guardar el archivo
con BOM UTF-8 también rompe el `reset` con un error de JSON que no menciona el BOM.

## 6. Primera ejecución

Antes de activar el cron, ejecuta una vez a mano con parámetros baratos:

```bash
databricks jobs run-now --job-id 584295967848266 \
  --notebook-params '{"hashtags":"databricks","results_per_hashtag":"10","max_charge_usd":"0.5"}' \
  --profile <PERFIL>
```

Verifica el resultado:

```bash
databricks jobs list-runs --job-id 584295967848266 --profile <PERFIL>
databricks jobs get-run-output <TASK_RUN_ID> --profile <PERFIL>
```

La salida del notebook debe traer los conteos de bronze, silver, gold y
`viral_candidates`. Si todo cuadra, activa el cron editando `pause_status` a
`UNPAUSED` en `jobs/ingest_daily.job.json` y volviendo a aplicar el `reset`.

## Descarga de videos propios

El job `tiktok_download_own_video` recibe `urls`, `own_handles` y `output_dir`
como parámetros de job; la tarea los pasa a los widgets del notebook con
`{{job.parameters.<nombre>}}` y `base_parameters`. Solo descarga si el autor del
video está en `own_handles`; el resto queda como `skipped` y, por diseño, el
notebook falla al final si hubo URLs omitidas o con error. Ajusta `own_handles` a
tus cuentas con `--own-handles` al renderizar el JSON.

El job usa un entorno con `environment_version: 6` para poder instalar
`yt-dlp` y `curl_cffi`. Si tu workspace no soporta entornos serverless, cámbialo
a un clúster clásico y añade las librerías como dependencias del clúster.
