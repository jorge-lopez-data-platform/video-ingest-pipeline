# Databricks notebook source
# MAGIC %md
# MAGIC # Upload an owner video to the TikTok inbox as a DRAFT (production job task)
# MAGIC
# MAGIC Uses `inbox/video/init/`, which only needs the `video.upload` scope. The owner finishes and
# MAGIC publishes the draft from the TikTok app (caption, privacy, AI-generated label).
# MAGIC
# MAGIC Safety rules enforced here:
# MAGIC - Only `.mp4` files directly inside `/Volumes/tiktok/default/videos/`.
# MAGIC - Only files listed in the `own_videos` allowlist. Anything else (e.g. scraped third-party
# MAGIC   videos that share the volume) is rejected before any network call.
# MAGIC - The same file (SHA-256) is never submitted twice unless `allow_resubmit=true`.
# MAGIC - Access tokens are refreshed automatically; every upload is logged to
# MAGIC   `tiktok.default.upload_drafts`. The token is never printed.

# COMMAND ----------

import hashlib
import os
import time

import requests
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("video_path", "jesucitos_paraiso.mp4")
dbutils.widgets.text(
    "own_videos",
    "jesucitos_paraiso.mp4,video_amorpropio.mp4,7270127155529452843.mp4",
)
dbutils.widgets.dropdown("dry_run", "false", ["false", "true"])
dbutils.widgets.dropdown("allow_resubmit", "false", ["false", "true"])

VIDEOS_DIR = "/Volumes/tiktok/default/videos"
SCOPE = "tiktok_pipeline"
API = "https://open.tiktokapis.com/v2"
TOKEN_URL = f"{API}/oauth/token/"
AUDIT_TABLE = "tiktok.default.upload_drafts"
# Registro en bronze: una fila por borrador enviado, enriquecida con el guion de
# gold.guiones_60s que lo originó. Es la tabla que se consulta para saber qué se
# subió y de qué guion salió.
UPLOADS_BRONZE = "tiktok.bronze.tiktok_uploads"
GUIONES_GOLD = "tiktok.gold.guiones_60s"
OK_STATES = {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"}
# El inbox de TikTok tiene un cupo de borradores pendientes. Cuando se llena, el
# init responde este código: no es un fallo transitorio, así que reintentar solo
# quema ejecuciones. Se registra y se deja pasar para que el job horario siga vivo.
INBOX_FULL_CODES = {"spam_risk_too_many_pending_share"}


class InboxFull(Exception):
    """The TikTok inbox has too many unpublished drafts; retrying cannot help."""

w = WorkspaceClient()


def secret(key):
    return dbutils.secrets.get(SCOPE, key)


def as_bool(name):
    return dbutils.widgets.get(name).strip().lower() == "true"


# COMMAND ----------

# MAGIC %md ## 1. Validate the request (no network calls yet)

# COMMAND ----------

raw_path = dbutils.widgets.get("video_path").strip()
video_name = os.path.basename(raw_path)
video_path = raw_path if raw_path.startswith("/") else f"{VIDEOS_DIR}/{video_name}"

allowlist = {n.strip() for n in dbutils.widgets.get("own_videos").split(",") if n.strip()}

if not video_name.endswith(".mp4"):
    raise ValueError(f"video_path must be an .mp4: {video_name}")
if os.path.dirname(video_path) != VIDEOS_DIR:
    raise ValueError(f"video_path must live directly inside {VIDEOS_DIR}")
if video_name not in allowlist:
    raise ValueError(
        f"{video_name} is not in the own_videos allowlist. "
        f"Allowed: {sorted(allowlist)}. Add it explicitly only if the owner recorded it."
    )
if not os.path.isfile(video_path):
    raise FileNotFoundError(video_path)

video_size = os.path.getsize(video_path)
with open(video_path, "rb") as f:
    sha256 = hashlib.sha256(f.read()).hexdigest()

DRY_RUN = as_bool("dry_run")
ALLOW_RESUBMIT = as_bool("allow_resubmit")


def current_run_id():
    """Return the run id, or "unknown" where the API is not available.

    On serverless, `currentRunId()` raises Py4JSecurityException because the
    method is not whitelisted on CommandContext. The audit column would just
    be empty, but the exception killed the task before the upload started, so
    the lookup must never be able to fail the run.
    """
    if DRY_RUN:
        return "dry-run"
    try:
        text = dbutils.notebook.entry_point.getDbutils().notebook().getContext().currentRunId().toString()
        if text and text.isdigit():
            return text
    except Exception as exc:
        print(f"currentRunId unavailable ({type(exc).__name__}); trying run-id env vars.")
    for key in ("DATABRICKS_RUN_ID", "DB_RUN_ID", "DATABRICKS_JOB_RUN_ID"):
        value = os.environ.get(key, "")
        if value.isdigit():
            return value
    print("No run id available; auditing as 'unknown'.")
    return "unknown"


RUN_ID = current_run_id()

print(f"File:     {video_path}")
print(f"Size:     {video_size / 1e6:.2f} MB")
print(f"SHA-256:  {sha256}")
print(f"Run:      {RUN_ID} | dry_run={DRY_RUN} | allow_resubmit={ALLOW_RESUBMIT}")


def load_previous_uploads():
    """Return prior successful uploads for this file, or [] if the audit table does not exist yet."""
    try:
        return spark.sql(
            f"""SELECT publish_id, status, uploaded_at
                FROM {AUDIT_TABLE}
                WHERE video_sha256 = '{sha256}' AND dry_run = false
                  AND status IN ('SEND_TO_USER_INBOX', 'PUBLISH_COMPLETE')"""
        ).collect()
    except Exception as exc:  # audit table is best-effort, never blocks the upload
        print(f"Audit table not readable yet ({type(exc).__name__}); treating as first upload.")
        return []


if not ALLOW_RESUBMIT:
    previous = load_previous_uploads()
    if previous:
        raise ValueError(
            "This exact file was already sent to the TikTok inbox "
            f"(publish_id={previous[0]['publish_id']} at {previous[0]['uploaded_at']}). "
            "Pass allow_resubmit=true only if the owner really wants a second draft."
        )

# COMMAND ----------

# MAGIC %md ## 2. Token: reuse, refresh only when the API says it is expired

# COMMAND ----------


def token_request(extra):
    body = {
        "client_key": secret("tiktok_client_key"),
        "client_secret": secret("tiktok_client_secret"),
        **extra,
    }
    r = requests.post(
        TOKEN_URL,
        data=body,
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


def refresh_access_token():
    payload = token_request(
        {"grant_type": "refresh_token", "refresh_token": secret("tiktok_refresh_token")}
    )
    w.secrets.put_secret(
        scope=SCOPE, key="tiktok_access_token", string_value=payload["access_token"]
    )
    if payload.get("refresh_token"):
        w.secrets.put_secret(
            scope=SCOPE, key="tiktok_refresh_token", string_value=payload["refresh_token"]
        )
    print("Access token refreshed and stored.")
    return payload["access_token"]


TOKEN = secret("tiktok_access_token")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json; charset=UTF-8"}
TOKEN_ERRORS = {"access_token_invalid", "access_token_expired", "token_expired"}


def api_post(path, payload, retry_on_token_error=True):
    """POST to the TikTok API, refreshing the access token once if it is reported as expired."""
    global TOKEN
    resp = requests.post(f"{API}{path}", headers=HEADERS, json=payload, timeout=120).json()
    code = resp.get("error", {}).get("code", "ok")
    if code in TOKEN_ERRORS and retry_on_token_error:
        TOKEN = refresh_access_token()
        HEADERS["Authorization"] = f"Bearer {TOKEN}"
        resp = requests.post(f"{API}{path}", headers=HEADERS, json=payload, timeout=120).json()
        code = resp.get("error", {}).get("code", "ok")
    if code != "ok":
        if code in INBOX_FULL_CODES:
            raise InboxFull(f"{path}: {code}")
        raise RuntimeError(f"{path} failed: {resp.get('error')}")
    return resp


def video_fuente_id(nombre):
    """Recupera el `video_fuente_id` del nombre `motivacional_<id>.mp4`."""
    base = os.path.splitext(nombre)[0]
    return base.split("_", 1)[1] if "_" in base else ""


def registrar_bronze(estado, detalle="", publish_id_=""):
    """Añade la fila a `tiktok.bronze.tiktok_uploads`, con el guion que la originó.

    Se escribe también cuando el inbox está lleno: sin esa fila no habría forma de
    saber que el video quedó generado y pendiente de subir. Un fallo aquí nunca
    debe tumbar la tarea.
    """
    fuente_id = video_fuente_id(video_name).replace("'", "''")
    name_sql = video_name.replace("'", "''")
    path_sql = video_path.replace("'", "''")
    detal_sql = detalle.replace("'", "''")
    pub_sql = publish_id_.replace("'", "''")
    try:
        spark.sql(
            f"""CREATE TABLE IF NOT EXISTS {UPLOADS_BRONZE} (
                  video_fuente_id STRING, video_name STRING, video_path STRING,
                  video_sha256 STRING, video_bytes BIGINT, publish_id STRING,
                  status STRING, fail_reason STRING, run_id STRING, uploaded_at TIMESTAMP,
                  video_fuente_url STRING, fuente_plays BIGINT, hook STRING, open_loop STRING,
                  bloque_1 STRING, pattern_interrupt STRING, bloque_2 STRING, pre_cierre STRING,
                  cierre STRING, cta STRING, hashtags STRING, texto_pantalla_1 STRING,
                  texto_pantalla_2 STRING, texto_pantalla_3 STRING, palabras_reales BIGINT,
                  duracion_estimada DOUBLE, confianza STRING, guion_generado_en TIMESTAMP,
                  logged_at TIMESTAMP
                ) USING DELTA"""
        )
        # LEFT JOIN y no INNER: los videos de jesucitos no salen de gold.guiones_60s
        # y aun así deben quedar registrados. El subselect de una fila garantiza que
        # siempre se inserta exactamente una fila.
        spark.sql(
            f"""INSERT INTO {UPLOADS_BRONZE}
                SELECT COALESCE(CAST(g.video_fuente_id AS STRING), '{fuente_id}') AS video_fuente_id,
                       '{name_sql}', '{path_sql}', '{sha256}',
                       {video_size}, '{pub_sql}', '{estado}',
                       '{detal_sql}', '{RUN_ID}', current_timestamp(),
                       g.video_fuente_url, g.fuente_plays, g.hook, g.open_loop, g.bloque_1,
                       g.pattern_interrupt, g.bloque_2, g.pre_cierre, g.cierre, g.cta,
                       g.hashtags, g.texto_pantalla_1, g.texto_pantalla_2, g.texto_pantalla_3,
                       g.palabras_reales, g.duracion_estimada, g.confianza, g.generado_en,
                       current_timestamp()
                FROM (SELECT '{fuente_id}' AS fuente_id) v
                LEFT JOIN {GUIONES_GOLD} g
                  ON CAST(g.video_fuente_id AS STRING) = v.fuente_id"""
        )
        print(f"Registrado en {UPLOADS_BRONZE}: {video_name} -> {estado}")
    except Exception as exc:  # el registro no debe ocultar el resultado de la subida
        print(f"WARNING: no se pudo registrar en {UPLOADS_BRONZE} "
              f"({type(exc).__name__}): {exc}")

# COMMAND ----------

# MAGIC %md ## 3. Dry run: verify credentials without uploading anything

# COMMAND ----------

if DRY_RUN:
    def user_info(token):
        return requests.get(
            f"{API}/user/info/",
            params={"fields": "open_id,display_name,follower_count,video_count"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        ).json()

    info = user_info(TOKEN)
    if info.get("error", {}).get("code") in TOKEN_ERRORS:
        info = user_info(refresh_access_token())
    if info.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"user/info failed: {info.get('error')}")
    user = info["data"]["user"]
    print(
        f"DRY RUN OK. Account: {user.get('display_name')} (open_id {user.get('open_id')}), "
        f"{user.get('follower_count')} followers, {user.get('video_count')} videos."
    )
    dbutils.notebook.exit("dry_run_ok")

# COMMAND ----------

# MAGIC %md ## 4. Init the inbox upload

# COMMAND ----------

try:
    init = api_post(
        "/post/publish/inbox/video/init/",
        {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,
                "total_chunk_count": 1,
            }
        },
    )
except InboxFull as exc:
    # El cupo de borradores pendientes está lleno. Reintentar cada minuto no lo
    # vacía (solo lo hace publicar/borrar desde la app), así que se registra el
    # motivo y se sale en verde: el job horario volverá a intentarlo más tarde.
    print(f"INBOX LLENO: {video_name} no se subió ({exc}).")
    registrar_bronze("SKIPPED_INBOX_FULL", str(exc))
    dbutils.notebook.exit(f"skipped_inbox_full:{video_name}")

publish_id = init["data"]["publish_id"]
upload_url = init["data"]["upload_url"]
print("publish_id:", publish_id)

# COMMAND ----------

with open(video_path, "rb") as f:
    data = f.read()

put = requests.put(
    upload_url,
    headers={
        "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        "Content-Type": "video/mp4",
    },
    data=data,
    timeout=600,
)
put.raise_for_status()
print("Upload PUT:", put.status_code)

# COMMAND ----------

# MAGIC %md ## 5. Wait for the draft to land in the inbox, then audit it

# COMMAND ----------

status = None
fail_reason = ""
for _ in range(30):
    resp = api_post("/post/publish/status/fetch/", {"publish_id": publish_id})
    data_ = resp.get("data", {})
    status = data_.get("status")
    fail_reason = data_.get("fail_reason") or ""
    print("Status:", status, fail_reason)
    if status in OK_STATES or status == "FAILED":
        break
    time.sleep(10)

print("publish_id:", publish_id, "| final status:", status)

try:
    spark.sql(
        f"""CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
              publish_id STRING, video_name STRING, video_path STRING, video_sha256 STRING,
              video_bytes BIGINT, status STRING, fail_reason STRING, run_id STRING,
              dry_run BOOLEAN, uploaded_at TIMESTAMP
            ) USING DELTA"""
    )
    spark.sql(
        f"""INSERT INTO {AUDIT_TABLE} VALUES (
              '{publish_id}', '{video_name}', '{video_path}', '{sha256}', {video_size},
              '{status}', '{fail_reason}', '{RUN_ID}', false, current_timestamp())"""
    )
    print(f"Audited in {AUDIT_TABLE}.")
except Exception as exc:  # audit failures must not hide a successful upload
    print(f"WARNING: could not write audit row ({type(exc).__name__}): {exc}")

registrar_bronze(status or "UNKNOWN", fail_reason, publish_id)

if status not in OK_STATES:
    raise RuntimeError(f"Draft upload did not complete: {status} ({fail_reason})")

# COMMAND ----------

# MAGIC %md ## 6. Avisar por Slack
# MAGIC
# MAGIC Solo se avisa de las subidas que llegaron al inbox. El mensaje lleva el
# MAGIC título y los hashtags del `.txt` hermano porque el endpoint de borrador no
# MAGIC los acepta: sin pegarlos a mano, el video no se publica.

# COMMAND ----------

def avisar_slack(nombre, publish_id_, estado, titulo, hashtags):
    """Publica el aviso en Slack. Un fallo aquí no debe marcar la subida como fallida.

    La URL del webhook vive en el secret scope y nunca se imprime. Si no está,
    se omite el aviso: notificar es secundario frente a haber subido el video.
    """
    try:
        webhook = dbutils.secrets.get(SCOPE, "slack_webhook_url")
    except Exception:
        print("AVISO: no hay slack_webhook_url en el scope; no se notifica a Slack.")
        return

    texto = (
        f"*Borrador subido a TikTok* :movie_camera:\n"
        f"• Video: `{nombre}`\n"
        f"• Estado: `{estado}`\n"
        f"• publish_id: `{publish_id_}`"
    )
    if titulo:
        texto += f"\n• Título: {titulo}"
    if hashtags:
        texto += f"\n• Hashtags: {hashtags}"
    texto += "\n_Publica desde la app: el borrador está en el inbox._"

    try:
        resp = requests.post(webhook, json={"text": texto}, timeout=20)
        resp.raise_for_status()
        print("Aviso enviado a Slack.")
    except Exception as exc:
        print(f"AVISO: no se pudo notificar a Slack ({type(exc).__name__}: {exc})")


# El `.txt` hermano lo escribe `generar_video_guion` con el título y los
# hashtags; se lee aquí para no repetir esa lógica ni volver a consultar Spark.
titulo_aviso, hashtags_aviso = "", ""
publicacion = os.path.join(VIDEOS_DIR, os.path.splitext(video_name)[0] + "_publicacion.txt")
try:
    with open(publicacion, encoding="utf-8") as f:
        lineas = [l.strip() for l in f.read().splitlines() if l.strip()]
    if lineas:
        titulo_aviso = lineas[0]
        hashtags_aviso = " ".join(lineas[1:])
except OSError:
    print("Sin fichero de publicación; el aviso irá sin título ni hashtags.")

avisar_slack(video_name, publish_id, status, titulo_aviso, hashtags_aviso)

print("Open the TikTok app on the authorized account: the draft is in the inbox/notifications.")
