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
databricks workspace import-dir notebooks /tiktok_ingest --profile <PERFIL>
databricks workspace import-dir ingest_lib /tiktok_ingest/ingest_lib --profile <PERFIL>
```

La segunda orden es obligatoria: el notebook `01_ingest_tiktok.py` importa
`ingest_lib.parse` desde su carpeta hermana en el workspace. Si falta, el run
muere con `ModuleNotFoundError: No module named 'ingest_lib'`.

Los jobs de `jobs/` traen la ruta con el placeholder `USUARIO`: sustituye
`/Users/USUARIO/` por `/Users/<tu-usuario>/` (tu correo del workspace) antes de
aplicarlos, o el `reset` fallará por ruta inexistente.
Si los importas en otra ruta, edita el `notebook_path` del JSON del job antes de
aplicarlo.

## 5. Crear los jobs

Los archivos de `jobs/` están en el formato que acepta `jobs reset`: un objeto
con `job_id` y `new_settings`.

```bash
# Job diario de ingesta
databricks jobs reset --json @jobs/ingest_daily.job.json --profile <PERFIL>

# Job que encadena ingesta + descarga de videos propios
databricks jobs reset --json @jobs/download_own_video.job.json --profile <PERFIL>
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

El job `tiktok_download_own_video` recibe `urls`, `own_handles` y `output_dir`.
Solo descarga si el autor del video está en `own_handles`; el resto queda como
`skipped`. Ajusta `own_handles` a tus cuentas antes de usarlo.

El job usa un entorno con `environment_version: 6` para poder instalar
`yt-dlp` y `curl_cffi`. Si tu workspace no soporta entornos serverless, cámbialo
a un clúster clásico y añade las librerías como dependencias del clúster.
