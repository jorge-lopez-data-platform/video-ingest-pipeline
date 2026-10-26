# Databricks notebook source
# MAGIC %md
# MAGIC # Publicar un video en YouTube Shorts (tarea del job)
# MAGIC
# MAGIC Sube el `.mp4` con `videos.insert` resumable y registra el resultado en
# MAGIC `tiktok.bronze.distribucion_video`.
# MAGIC
# MAGIC Reglas:
# MAGIC - Idempotente: si ya está `PUBLICADO` o `PUBLICADO_PRIVADO`, sale sin llamar a la API.
# MAGIC - `quotaExceeded` no se reintenta: es el techo diario de 10 000 unidades y se agota
# MAGIC   con 6 subidas. Se marca `SKIPPED_CUOTA` y se sigue.
# MAGIC - El título y los hashtags salen del `.txt` hermano que escribe `generar_video_guion`.
# MAGIC - Los tokens nunca se imprimen.

# COMMAND ----------

import json
import os
import sys

import requests
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("video_name", "")
dbutils.widgets.text("privacy", "private")

VIDEOS_DIR = "/Volumes/tiktok/default/videos"
LIB = f"{VIDEOS_DIR}/lib"
# Los renders nuevos viven en AAAA/MM/DD y los antiguos planos en la raíz.
sys.path.insert(0, LIB)
from scripts import rutas  # noqa: E402

SCOPE = "tiktok_pipeline"
DISTRIBUCION = "tiktok.bronze.distribucion_video"
CANAL = "youtube"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

w = WorkspaceClient()

video_name = dbutils.widgets.get("video_name").strip()
if not video_name:
    raise ValueError("video_name vacío")


def secret(key):
    return dbutils.secrets.get(SCOPE, key)


def run_id_actual():
    """Run id para la columna `run_id`, o "unknown" si no está disponible.

    En serverless `currentRunId()` puede no estar whitelisted; el rastro de
    auditoría nunca debe tumbar la publicación.
    """
    try:
        texto = (dbutils.notebook.entry_point.getDbutils().notebook()
                 .getContext().currentRunId().toString())
        if texto and texto.isdigit():
            return texto
    except Exception:
        pass
    return os.environ.get("DATABRICKS_RUN_ID", "unknown")


RUN_ID = run_id_actual()


# COMMAND ----------

# MAGIC %md ## 1. Estado previo: no repetir una subida que ya funcionó

# COMMAND ----------

def estado_actual():
    """Devuelve la fila de distribución de este video en este canal, o None."""
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
    """MERGE de la fila. Se actualiza, nunca se duplica: el grano es (video, canal).

    Un fallo al registrar no debe tumbar la publicación, que ya ocurrió.
    """
    nombre_sql = video_name.replace("'", "''")
    fuente = video_name.split("_", 1)[1].split(".")[0] if "_" in video_name else ""
    intento = 1 if incrementar else 0
    sql = f"""
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
    try:
        spark.sql(
            f"""CREATE TABLE IF NOT EXISTS {DISTRIBUCION} (
                  video_name STRING, video_fuente_id STRING, canal STRING, estado STRING,
                  id_externo STRING, url STRING, motivo_fallo STRING, run_id STRING,
                  intentos INT, creado_en TIMESTAMP, actualizado_en TIMESTAMP
                ) USING DELTA"""
        )
        spark.sql(sql)
        print(f"Registrado: {video_name} / {CANAL} -> {estado}")
    except Exception as exc:
        print(f"WARNING: no se pudo registrar en {DISTRIBUCION} "
              f"({type(exc).__name__}): {exc}")


previo = estado_actual()
if previo and previo["estado"] in ("PUBLICADO", "PUBLICADO_PRIVADO"):
    print(f"Ya estaba {previo['estado']} (id {previo['id_externo']}); no se sube de nuevo.")
    dbutils.notebook.exit(f"already_{previo['estado'].lower()}")

# COMMAND ----------

# MAGIC %md ## 2. Access token desde el refresh token (dura 1 h)

# COMMAND ----------

resp = requests.post(
    TOKEN_URL,
    data={
        "client_id": secret("youtube_client_id"),
        "client_secret": secret("youtube_client_secret"),
        "refresh_token": secret("youtube_refresh_token"),
        "grant_type": "refresh_token",
    },
    timeout=60,
)
if resp.status_code != 200:
    registrar("FALLIDO", motivo=f"token_{resp.status_code}", incrementar=True)
    raise RuntimeError(f"OAuth de YouTube devolvió {resp.status_code}: {resp.text[:200]}")

TOKEN = resp.json()["access_token"]
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# COMMAND ----------

# MAGIC %md ## 3. Título y descripción desde el `.txt` hermano

# COMMAND ----------

titulo, hashtags = "", ""
publicacion = rutas.companero(video_name, "_publicacion.txt", VIDEOS_DIR)
# `companero` devuelve None si el video no tiene .txt hermano, y `open(None)`
# lanza TypeError, que NO es OSError: el except de abajo no lo recogía y la
# iteración moría con "expected str, bytes or os.PathLike object, not NoneType".
try:
    if publicacion:
        with open(publicacion, encoding="utf-8") as f:
            lineas = [l.strip() for l in f.read().splitlines() if l.strip()]
        if lineas:
            titulo = lineas[0]
            hashtags = " ".join(lineas[1:])
except OSError:
    print("Sin fichero de publicación; se usa el nombre del fichero como título.")

# Sin título, YouTube rechaza el insert. Se cae al nombre del video.
titulo = (titulo.replace("'", "") or os.path.splitext(video_name)[0])[:100]
descripcion = ((hashtags or "") + "\n\n#Shorts").replace("'", "")

ruta = rutas.resolver(video_name, VIDEOS_DIR)
if not ruta:
    registrar("FALLIDO", motivo="file_not_found", incrementar=True)
    raise FileNotFoundError(os.path.join(VIDEOS_DIR, video_name))

tam = os.path.getsize(ruta)
privacidad = dbutils.widgets.get("privacy").strip() or "private"
print(f"Título: {titulo}")
print(f"Tamaño: {tam / 1e6:.2f} MB | privacidad: {privacidad}")

# COMMAND ----------

# MAGIC %md ## 4. Subida resumable en una sola sesión

# COMMAND ----------

cuerpo = {
    "snippet": {
        "title": titulo,
        "description": descripcion,
        "categoryId": "22",          # People & Blogs
        "defaultLanguage": "es",
    },
    "status": {
        "privacyStatus": privacidad,
        "selfDeclaredMadeForKids": False,
    },
}

# El init de la sesión resumable devuelve en `Location` la URL del binario.
init = requests.post(
    UPLOAD_URL,
    params={"uploadType": "resumable", "part": "snippet,status"},
    headers={**HEADERS, "Content-Type": "application/json; charset=UTF-8",
             "X-Upload-Content-Type": "video/mp4",
             "X-Upload-Content-Length": str(tam)},
    json=cuerpo,
    timeout=120,
)

if init.status_code >= 400:
    detalle, motivo_code = "", ""
    try:
        err = init.json().get("error", {})
        motivo_code = (err.get("errors") or [{}])[0].get("reason", "")
        detalle = err.get("message", "")[:300]
    except Exception:
        detalle = init.text[:300]

    # Cuota agotada: es el techo diario de la API, no un fallo del video.
    if motivo_code == "quotaExceeded":
        print(f"CUOTA AGOTADA: {detalle}")
        registrar("SKIPPED_CUOTA", motivo=motivo_code)
        dbutils.notebook.exit("skipped_quota")

    registrar("FALLIDO", motivo=motivo_code or f"http_{init.status_code}", incrementar=True)
    raise RuntimeError(f"videos.insert init falló ({init.status_code}): {detalle}")

with open(ruta, "rb") as f:
    subida = requests.put(
        init.headers["Location"],
        headers={**HEADERS, "Content-Type": "video/mp4", "Content-Length": str(tam)},
        data=f,
        timeout=900,
    )

if subida.status_code >= 300:
    registrar("FALLIDO", motivo=f"upload_{subida.status_code}", incrementar=True)
    raise RuntimeError(f"subida falló ({subida.status_code}): {subida.text[:300]}")

datos = subida.json()
video_id = datos.get("id", "")
privacidad_final = (datos.get("status") or {}).get("privacyStatus", "")
estado = "PUBLICADO" if privacidad_final == "public" else "PUBLICADO_PRIVADO"
url = f"https://youtube.com/shorts/{video_id}" if video_id else ""

print(f"videoId: {video_id} | privacidad real: {privacidad_final}")
registrar(estado, id_externo=video_id, url=url)

dbutils.notebook.exit(json.dumps({"video_id": video_id, "estado": estado}))
