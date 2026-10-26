# Databricks notebook source
# MAGIC %md
# MAGIC # Publicar un video propio en TikTok con Direct Post (tarea del job)
# MAGIC
# MAGIC Vía `post/publish/video/init/`, que **publica sin intervención humana** y no
# MAGIC consume el cupo de borradores del inbox (5 pendientes / 24 h). Es la única
# MAGIC vía que permite superar esas 5 piezas diarias.
# MAGIC
# MAGIC ## Requisito externo: la app debe estar auditada
# MAGIC
# MAGIC Mientras la app «Lomasviral» no pase la auditoría de Content Posting, TikTok
# MAGIC responde `unaudited_client_can_only_post_to_private_accounts` y aquí se
# MAGIC registra como `BLOQUEADO_AUDITORIA` sin reintentar. El código ya está listo:
# MAGIC cuando la auditoría se apruebe, el mismo notebook empieza a publicar sin
# MAGIC cambios.
# MAGIC
# MAGIC ## Reglas
# MAGIC - Idempotente: si el video ya está `PUBLICADO` en este canal, sale sin llamar a la API.
# MAGIC - Consulta `creator_info/query` antes de publicar: es obligatorio y da la lista
# MAGIC   de `privacy_level_options` que la cuenta admite.
# MAGIC - Los errores deterministas (`spam_risk_*`, `unaudited_*`, `scope_*`) no se
# MAGIC   reintentan: se registran y se sigue.
# MAGIC - El token nunca se imprime.

# COMMAND ----------

import os
import sys
import time

import requests
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("video_name", "")
# Deja vacío para usar la primera opción que ofrezca TikTok para esta cuenta.
dbutils.widgets.text("privacy_level", "")
dbutils.widgets.dropdown("publicar", "true", ["true", "false"])

VIDEOS_DIR = "/Volumes/tiktok/default/videos"
LIB = f"{VIDEOS_DIR}/lib"
# Los renders nuevos viven en AAAA/MM/DD y los antiguos planos en la raíz.
sys.path.insert(0, LIB)
from scripts import rutas  # noqa: E402

SCOPE = "tiktok_pipeline"
API = "https://open.tiktokapis.com/v2"
TOKEN_URL = f"{API}/oauth/token/"
DISTRIBUCION = "tiktok.bronze.distribucion_video"
CANAL = "tiktok"
OK_STATES = {"PUBLISH_COMPLETE"}

# Errores que no mejoran reintentando: se registran y se sale en verde.
BLOQUEOS = {
    "unaudited_client_can_only_post_to_private_accounts": "BLOQUEADO_AUDITORIA",
    "spam_risk_too_many_posts": "SKIPPED_CUOTA",
    "spam_risk_user_banned_from_posting": "BLOQUEADO_CUENTA",
    "scope_not_authorized": "SIN_SCOPE",
}

w = WorkspaceClient()

video_name = dbutils.widgets.get("video_name").strip()
if not video_name:
    raise ValueError("video_name vacío")
PUBLICAR = dbutils.widgets.get("publicar").strip().lower() == "true"
PRIVACY_PEDIDA = dbutils.widgets.get("privacy_level").strip()


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

video_path = rutas.resolver(video_name, VIDEOS_DIR)
if not video_path:
    raise FileNotFoundError(f"{video_name} no está en {VIDEOS_DIR}")
video_size = os.path.getsize(video_path)
print(f"Video: {video_path} ({video_size / 1e6:.2f} MB) | publicar={PUBLICAR}")

# COMMAND ----------

# MAGIC %md ## 1. Estado previo: no repetir una publicación que ya funcionó

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
    print(f"Ya estaba {previo['estado']} (id {previo['id_externo']}); no se publica de nuevo.")
    dbutils.notebook.exit(f"already_{previo['estado'].lower()}")

if not PUBLICAR:
    print("publicar=false: solo se comprueba el estado. No se publica nada.")
    dbutils.notebook.exit("publicar_false")

# COMMAND ----------

# MAGIC %md ## 2. Token y `creator_info` (obligatorio antes de publicar)

# COMMAND ----------


def token_request(extra):
    r = requests.post(
        TOKEN_URL,
        data={
            "client_key": secret("tiktok_client_key"),
            "client_secret": secret("tiktok_client_secret"),
            **extra,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    payload = r.json()
    if r.status_code != 200 or "access_token" not in payload:
        raise RuntimeError(
            f"TikTok token endpoint error ({r.status_code}): "
            f"{payload.get('error')} - {payload.get('error_description')}"
        )
    return payload


payload = token_request(
    {"grant_type": "refresh_token", "refresh_token": secret("tiktok_refresh_token")}
)
w.secrets.put_secret(scope=SCOPE, key="tiktok_access_token",
                     string_value=payload["access_token"])
if payload.get("refresh_token"):
    w.secrets.put_secret(scope=SCOPE, key="tiktok_refresh_token",
                         string_value=payload["refresh_token"])
print("Scopes concedidos:", payload.get("scope"))

HEADERS = {
    "Authorization": f"Bearer {payload['access_token']}",
    "Content-Type": "application/json; charset=UTF-8",
}

info = requests.post(f"{API}/post/publish/creator_info/query/",
                     headers=HEADERS, json={}, timeout=60).json()
codigo = info.get("error", {}).get("code", "ok")
if codigo != "ok":
    registrar(BLOQUEOS.get(codigo, "FALLIDO"), motivo=codigo)
    print(f"creator_info falló: {info.get('error')}")
    dbutils.notebook.exit(f"creator_info_{codigo}")

opciones = info["data"].get("privacy_level_options") or []
print("privacy_level_options:", opciones)

if PRIVACY_PEDIDA:
    if PRIVACY_PEDIDA not in opciones:
        registrar("FALLIDO", motivo=f"privacy_level no admitido: {PRIVACY_PEDIDA}")
        raise ValueError(f"privacy_level={PRIVACY_PEDIDA} no está en {opciones}")
    privacy = PRIVACY_PEDIDA
elif opciones:
    # La primera opción es la más abierta que la cuenta admite. Con la app sin
    # auditar, TikTok solo ofrece SELF_ONLY.
    privacy = opciones[0]
else:
    raise RuntimeError("TikTok no devolvió ninguna opción de privacidad.")

print("privacy_level elegido:", privacy)

# COMMAND ----------

# MAGIC %md ## 3. Iniciar el Direct Post

# COMMAND ----------

# El título y los hashtags salen del `.txt` hermano que escribe
# `generar_video_guion`. Direct Post sí los acepta (el inbox no), así que este es
# el único camino en el que la publicación queda completa sin tocar la app.
titulo, hashtags = "", ""
publicacion = rutas.companero(video_name, "_publicacion.txt", VIDEOS_DIR)
if publicacion:
    with open(publicacion, encoding="utf-8") as f:
        lineas = [l.strip() for l in f.read().splitlines() if l.strip()]
    if lineas:
        titulo = lineas[0]
        hashtags = " ".join(lineas[1:])
else:
    print("Sin .txt hermano: se publica sin título ni hashtags.")

# TikTok limita el título a 2200 caracteres y no admite saltos de línea.
titulo_final = " ".join((titulo + " " + hashtags).split())[:2200]
print(f"Título: {titulo_final[:120]}{'...' if len(titulo_final) > 120 else ''}")

init = requests.post(
    f"{API}/post/publish/video/init/",
    headers=HEADERS,
    json={
        "post_info": {
            "title": titulo_final,
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1,
        },
    },
    timeout=120,
).json()

codigo = init.get("error", {}).get("code", "ok")
if codigo != "ok":
    estado = BLOQUEOS.get(codigo, "FALLIDO")
    registrar(estado, motivo=codigo)
    print(f"init rechazado: {estado} ({init.get('error')})")
    # Los bloqueos no son fallos del pipeline: se sale en verde para no disparar
    # reintentos que TikTok leería como ráfaga.
    dbutils.notebook.exit(f"{estado.lower()}:{codigo}")

publish_id = init["data"]["publish_id"]
upload_url = init["data"]["upload_url"]
print("publish_id:", publish_id)

# COMMAND ----------

# MAGIC %md ## 4. Subir el binario y esperar el estado final

# COMMAND ----------

with open(video_path, "rb") as f:
    datos = f.read()

put = requests.put(
    upload_url,
    headers={
        "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        "Content-Type": "video/mp4",
    },
    data=datos,
    timeout=600,
)
put.raise_for_status()
print("Upload PUT:", put.status_code)

estado, motivo = None, ""
for _ in range(60):
    resp = requests.post(f"{API}/post/publish/status/fetch/", headers=HEADERS,
                         json={"publish_id": publish_id}, timeout=60).json()
    datos_estado = resp.get("data", {})
    estado = datos_estado.get("status")
    motivo = datos_estado.get("fail_reason") or ""
    print("Status:", estado, motivo)
    if estado in OK_STATES or estado in ("FAILED", "PUBLISH_COMPLETE"):
        break
    time.sleep(10)

if estado in OK_STATES:
    registrar("PUBLICADO", id_externo=publish_id)
else:
    registrar("FALLIDO", id_externo=publish_id, motivo=motivo or str(estado),
              incrementar=True)
    raise RuntimeError(f"Direct Post no completó: {estado} ({motivo})")

print(f"Publicado en TikTok. publish_id={publish_id}")
