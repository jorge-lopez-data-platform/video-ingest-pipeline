# Databricks notebook source
# MAGIC %md
# MAGIC # Publicar un video como Reel en una página de Facebook (tarea del job)
# MAGIC
# MAGIC Usa la Graph API de vídeo en tres fases —`start`, subida binaria, `finish`—
# MAGIC y registra el resultado en `tiktok.bronze.distribucion_video`.
# MAGIC
# MAGIC Facebook es el **único** canal de los tres que publica en público sin auditoría
# MAGIC previa, así que aquí sí se usa `video_state=PUBLISHED`.
# MAGIC
# MAGIC Reglas:
# MAGIC - Idempotente: si ya está `PUBLICADO`, sale sin llamar a la API.
# MAGIC - Un Reel con error de procesado se registra como `FALLIDO`, sin reintentar en bucle.
# MAGIC - El token de página nunca se imprime.

# COMMAND ----------

import json
import os
import sys
import time

import requests
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("video_name", "")
dbutils.widgets.text("publicar", "true", "Si es false solo sube y deja el reel en borrador")

VIDEOS_DIR = "/Volumes/tiktok/default/videos"
LIB = f"{VIDEOS_DIR}/lib"
SCOPE = "tiktok_pipeline"
DISTRIBUCION = "tiktok.bronze.distribucion_video"
CANAL = "facebook"

# Los renders nuevos se agrupan en `AAAA/MM/DD`; `rutas.resolver` busca en la
# raíz y en esas carpetas. Ver la comprobación de existencia más abajo.
sys.path.insert(0, LIB)
from scripts.util import rutas  # noqa: E402

w = WorkspaceClient()

video_name = dbutils.widgets.get("video_name").strip()
if not video_name:
    raise ValueError("video_name vacío")


def secret(key):
    return dbutils.secrets.get(SCOPE, key)


def run_id_actual():
    """Run id para la columna `run_id`, o "unknown" si no está disponible."""
    try:
        texto = (dbutils.notebook.entry_point.getDbutils().notebook()
                 .getContext().currentRunId().toString())
        if texto and texto.isdigit():
            return texto
    except Exception:
        pass
    return os.environ.get("DATABRICKS_RUN_ID", "unknown")


RUN_ID = run_id_actual()

GRAPH_VERSION = secret("fb_graph_version")
PAGE_ID = secret("fb_page_id")
PAGE_TOKEN = secret("fb_page_access_token")
API = f"https://graph.facebook.com/{GRAPH_VERSION}"

# COMMAND ----------

# MAGIC %md ## 1. Estado previo: no repetir una publicación que ya funcionó

# COMMAND ----------

def estado_actual():
    nombre_sql = video_name.replace("'", "''")
    try:
        filas = spark.sql(
            f"""SELECT estado, id_externo FROM {DISTRIBUCION}
                WHERE video_name = '{nombre_sql}' AND canal = '{CANAL}'"""
        ).collect()
    except Exception as exc:
        print(f"Aviso: {DISTRIBUCION} no legible ({type(exc).__name__}); sigo sin estado.")
        return None
    return filas[0] if filas else None


def registrar(estado, id_externo="", url="", motivo="", incrementar=False):
    """MERGE de la fila. Se actualiza, nunca se duplica: el grano es (video, canal)."""
    nombre_sql = video_name.replace("'", "''")
    fuente = video_name.split("_", 1)[1].split(".")[0] if "_" in video_name else ""
    intento = 1 if incrementar else 0
    try:
        spark.sql(
            f"""CREATE TABLE IF NOT EXISTS {DISTRIBUCION} (
                  video_name STRING, video_fuente_id STRING, canal STRING, estado STRING,
                  id_externo STRING, url STRING, motivo_fallo STRING, run_id STRING,
                  intentos INT, creado_en TIMESTAMP, actualizado_en TIMESTAMP
                ) USING DELTA"""
        )
        spark.sql(
            f"""
            MERGE INTO {DISTRIBUCION} t
            USING (SELECT '{nombre_sql}' AS video_name, '{CANAL}' AS canal) s
            ON t.video_name = s.video_name AND t.canal = s.canal
            WHEN MATCHED THEN UPDATE SET
                t.estado = '{estado}',
                t.id_externo = COALESCE(NULLIF('{id_externo}', ''), t.id_externo),
                t.url = COALESCE(NULLIF('{url}', ''), t.url),
                t.motivo_fallo = '{motivo}',
                t.intentos = t.intentos + {intento},
                t.actualizado_en = current_timestamp()
            WHEN NOT MATCHED THEN INSERT
                (video_name, video_fuente_id, canal, estado, id_externo, url,
                 motivo_fallo, run_id, intentos, creado_en, actualizado_en)
            VALUES ('{nombre_sql}', '{fuente}', '{CANAL}', '{estado}',
                    '{id_externo}', '{url}', '{motivo}',
                    '{RUN_ID}', {intento}, current_timestamp(), current_timestamp())
            """
        )
        print(f"Registrado: {video_name} / {CANAL} -> {estado}")
    except Exception as exc:
        print(f"WARNING: no se pudo registrar en {DISTRIBUCION} "
              f"({type(exc).__name__}): {exc}")


def detalle_error(resp):
    """Mensaje de la Graph API, recortado, para `motivo_fallo`.

    Guardar solo `start_400` no dice nada: el 400 puede ser token caducado,
    permisos de la página, límite de tasa o vídeo rechazado. La Graph API
    devuelve `error.message` y `error.error_user_msg`, y sin ellos hubo que
    diagnosticar 13 fallos a ciegas. Se recorta porque la columna es corta y
    el MERGE interpola el valor en SQL.
    """
    try:
        err = resp.json().get("error") or {}
        partes = [err.get("message", ""), err.get("error_user_msg", ""),
                  f"code={err.get('code')}" if err.get("code") else "",
                  f"subcode={err.get('error_subcode')}" if err.get("error_subcode") else ""]
        texto = " | ".join(p for p in partes if p)
    except Exception:
        texto = (resp.text or "")[:200]
    # El MERGE interpola en una cadena SQL: una comilla simple rompería la
    # consulta y el fallo se perdería justo cuando más falta hace.
    return (texto or "sin_detalle").replace("'", "''")[:400]


previo = estado_actual()
if previo and previo["estado"] == "PUBLICADO":
    print(f"Ya estaba PUBLICADO (id {previo['id_externo']}); no se publica de nuevo.")
    dbutils.notebook.exit("already_publicado")

# COMMAND ----------

# MAGIC %md ## 2. Ficha del video: descripción desde el `.txt` hermano

# COMMAND ----------

descripcion = ""
publicacion = os.path.join(VIDEOS_DIR, os.path.splitext(video_name)[0] + "_publicacion.txt")
try:
    with open(publicacion, encoding="utf-8") as f:
        lineas = [l.strip() for l in f.read().splitlines() if l.strip()]
    if lineas:
        descripcion = " ".join(lineas)
except OSError:
    print("Sin fichero de publicación; el reel irá sin descripción.")

ruta = rutas.resolver(video_name, VIDEOS_DIR)
if not ruta:
    # Buscar solo en la raíz dejaba fuera TODOS los renders nuevos, que se
    # guardan en `AAAA/MM/DD`: el 2026-10-25 el mp4 existía y la tarea lo daba
    # por perdido con `file_not_found`.
    registrar("FALLIDO", motivo="file_not_found", incrementar=True)
    raise FileNotFoundError(f"{video_name} no está en {VIDEOS_DIR}")

tam = os.path.getsize(ruta)
print(f"Tamaño: {tam / 1e6:.2f} MB")

# COMMAND ----------

# MAGIC %md ## 3. Fase `start`: abrir la sesión de subida

# COMMAND ----------

inicio = requests.post(
    f"{API}/{PAGE_ID}/video_reels",
    data={"upload_phase": "start", "access_token": PAGE_TOKEN},
    timeout=120,
)
if inicio.status_code >= 400:
    registrar("FALLIDO", motivo=f"start_{inicio.status_code} {detalle_error(inicio)}",
              incrementar=True)
    raise RuntimeError(f"start falló ({inicio.status_code}): {inicio.text[:300]}")

datos_inicio = inicio.json()
video_id = datos_inicio.get("video_id", "")
upload_url = datos_inicio.get("upload_url", "")
print(f"video_id: {video_id}")

if not video_id or not upload_url:
    registrar("FALLIDO", motivo="start_sin_ids", incrementar=True)
    raise RuntimeError(f"start no devolvió video_id/upload_url: {datos_inicio}")

# COMMAND ----------

# MAGIC %md ## 4. Subida binaria al host `rupload`

# COMMAND ----------

with open(ruta, "rb") as f:
    subida = requests.post(
        upload_url,
        headers={
            # El host rupload usa `OAuth`, no `Bearer`, y no admite query string.
            "Authorization": f"OAuth {PAGE_TOKEN}",
            "offset": "0",
            "file_size": str(tam),
            "Content-Type": "application/octet-stream",
        },
        data=f,
        timeout=900,
    )

if subida.status_code >= 300:
    registrar("FALLIDO", motivo=f"upload_{subida.status_code} {detalle_error(subida)}",
              incrementar=True)
    raise RuntimeError(f"subida falló ({subida.status_code}): {subida.text[:300]}")

print("Subida binaria:", subida.status_code)

# COMMAND ----------

# MAGIC %md ## 5. Fase `finish`: publicar o dejar en borrador

# COMMAND ----------

publicar = dbutils.widgets.get("publicar").strip().lower() != "false"
fin = requests.post(
    f"{API}/{PAGE_ID}/video_reels",
    data={
        "upload_phase": "finish",
        "video_id": video_id,
        "video_state": "PUBLISHED" if publicar else "DRAFT",
        "description": descripcion,
        "access_token": PAGE_TOKEN,
    },
    timeout=180,
)

if fin.status_code >= 400:
    registrar("FALLIDO", motivo=f"finish_{fin.status_code} {detalle_error(fin)}",
              incrementar=True)
    raise RuntimeError(f"finish falló ({fin.status_code}): {fin.text[:300]}")

print("finish:", fin.status_code, fin.text[:200])

# COMMAND ----------

# MAGIC %md ## 6. Esperar el procesado

# COMMAND ----------

# Publicar es inmediato, pero el vídeo aún se procesa. Se sondea un rato corto:
# si no termina, el reel ya está publicado igualmente y el estado no debe pasar
# a FALLIDO por lentitud del procesado.
estado_proceso = ""
detalle_proceso = ""
for _ in range(20):
    respuesta = requests.get(
        f"{API}/{video_id}",
        params={"fields": "status", "access_token": PAGE_TOKEN},
        timeout=60,
    )
    # Si la consulta falla (token, permisos), `.json()` devolvería un `error`
    # en vez de `status` y el bucle se quedaría 5 minutos sondeando en vano
    # sin decir por qué. Se corta y se guarda el motivo.
    if respuesta.status_code >= 400:
        detalle_proceso = detalle_error(respuesta)
        print("consulta de estado falló:", respuesta.status_code, detalle_proceso)
        break
    consulta = respuesta.json()
    estado_proceso = (consulta.get("status") or {}).get("video_status", "")
    print("status:", estado_proceso)
    if estado_proceso in ("ready", "complete", "published", "processing_failed", "error"):
        break
    time.sleep(15)

if detalle_proceso:
    registrar("FALLIDO", id_externo=video_id,
              motivo=f"consulta_estado {detalle_proceso}", incrementar=True)
    raise RuntimeError(f"no se pudo consultar el estado del reel: {detalle_proceso}")

if estado_proceso in ("processing_failed", "error"):
    registrar("FALLIDO", id_externo=video_id,
              motivo=f"procesado_{estado_proceso}", incrementar=True)
    raise RuntimeError(f"Facebook no pudo procesar el reel: {estado_proceso}")

estado = "PUBLICADO" if publicar else "PUBLICADO_PRIVADO"
url = f"https://www.facebook.com/reel/{video_id}"
registrar(estado, id_externo=video_id, url=url)

dbutils.notebook.exit(json.dumps({
    "video_id": video_id,
    "estado": estado,
    "procesado": estado_proceso,
}))
