# Databricks notebook source
# MAGIC %md
# MAGIC # Subir un video propio al inbox de TikTok como BORRADOR (tarea de producción)
# MAGIC
# MAGIC Usa `inbox/video/init/`, que solo necesita el scope `video.upload`. El dueño
# MAGIC termina y publica el borrador desde la app de TikTok (caption, privacidad,
# MAGIC etiqueta de IA).
# MAGIC
# MAGIC ## Por qué este notebook consulta el cupo antes de llamar a la API
# MAGIC
# MAGIC TikTok documenta en la referencia de `inbox/video/init/` que «there may be at
# MAGIC most 5 pending shares within any 24-hour period». Al pasarse responde
# MAGIC `spam_risk_too_many_pending_share` (HTTP 403).
# MAGIC
# MAGIC Ese código **no es transitorio**: reintentarlo no lo arregla y una ráfaga de
# MAGIC reintentos es justo el patrón que TikTok marca como abuso automatizado. El
# MAGIC 2026-10-24 se hicieron 14 `init` en 4 minutos con el cupo ya agotado y solo 2
# MAGIC llegaron. Por eso aquí se comprueba el cupo con lo ya registrado en bronze y,
# MAGIC si no queda, se sale sin gastar una sola petición.
# MAGIC
# MAGIC ## Reglas de seguridad
# MAGIC - Solo `.mp4` dentro de `/Volumes/tiktok/default/videos/` (raíz o `AAAA/MM/DD`).
# MAGIC - Solo ficheros de la allowlist `own_videos`; cualquier otro (p. ej. los
# MAGIC   videos de terceros que comparten el volumen) se rechaza antes de la red.
# MAGIC - El mismo fichero (SHA-256) no se reenvía salvo `allow_resubmit=true`.
# MAGIC - El token se refresca solo y nunca se imprime.

# COMMAND ----------

import hashlib
import os
import sys
import time
from datetime import datetime, timezone

import requests
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("video_path", "jesucitos_paraiso.mp4")
dbutils.widgets.text(
    "own_videos",
    "jesucitos_paraiso.mp4,video_amorpropio.mp4,7270127155529452843.mp4",
)
dbutils.widgets.dropdown("dry_run", "false", ["false", "true"])
dbutils.widgets.dropdown("allow_resubmit", "false", ["false", "true"])
# Salta la comprobación de cupo. Solo para una subida manual y consciente: el
# cupo de TikTok no se puede saltar, pero sí se puede decidir intentarlo igual.
dbutils.widgets.dropdown("ignorar_cupo", "false", ["false", "true"])

VIDEOS_DIR = "/Volumes/tiktok/default/videos"
LIB = f"{VIDEOS_DIR}/lib"
# Los renders nuevos viven en AAAA/MM/DD y los antiguos planos en la raíz: la
# ruta se resuelve por nombre, nunca se concatena.
sys.path.insert(0, LIB)
from scripts import cuota, rutas  # noqa: E402

SCOPE = "tiktok_pipeline"
API = "https://open.tiktokapis.com/v2"
TOKEN_URL = f"{API}/oauth/token/"
AUDIT_TABLE = "tiktok.default.upload_drafts"
# Registro en bronze: una fila por intento de borrador, enriquecida con el guion
# de gold.guiones_60s que lo originó. Es la tabla que se consulta para saber qué
# se subió, de qué guion salió y cuánto cupo se ha gastado.
UPLOADS_BRONZE = "tiktok.bronze.tiktok_uploads"
GUIONES_GOLD = "tiktok.gold.guiones_60s"
OK_STATES = {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"}
# El inbox de TikTok tiene un cupo de borradores pendientes. Cuando se llena, el
# init responde este código: no es un fallo transitorio, así que reintentar solo
# quema ejecuciones y arriesga una sanción por ráfaga.
INBOX_FULL_CODES = {"spam_risk_too_many_pending_share"}
# Estados que consumen cupo: una subida aceptada ocupa un hueco hasta que el
# dueño publica o borra el borrador, y eso no se puede observar desde aquí.
ESTADOS_QUE_OCUPAN = ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE")


class InboxFull(Exception):
    """The TikTok inbox has too many unpublished drafts; retrying cannot help."""


w = WorkspaceClient()


def secret(key):
    return dbutils.secrets.get(SCOPE, key)


def as_bool(name):
    return dbutils.widgets.get(name).strip().lower() == "true"


# COMMAND ----------

# MAGIC %md ## 1. Validar la petición (todavía sin llamadas de red)

# COMMAND ----------

raw_path = dbutils.widgets.get("video_path").strip()
video_name = os.path.basename(raw_path)

# El widget llega solo con el nombre desde el job, así que la ruta se resuelve
# buscando en la raíz y en las carpetas AAAA/MM/DD.
video_path = raw_path if raw_path.startswith("/") else None
if not video_path:
    video_path = rutas.resolver(video_name, VIDEOS_DIR) or f"{VIDEOS_DIR}/{video_name}"

allowlist = {n.strip() for n in dbutils.widgets.get("own_videos").split(",") if n.strip()}

if not video_name.endswith(".mp4"):
    raise ValueError(f"video_path must be an .mp4: {video_name}")
# Ya no se exige que cuelgue de la raíz: se exige que cuelgue del volumen. Se
# comprueba con la ruta real para que un `../` no salga del volumen.
real = os.path.realpath(video_path)
if not real.startswith(os.path.realpath(VIDEOS_DIR) + os.sep):
    raise ValueError(f"video_path must live inside {VIDEOS_DIR}")
if video_name not in allowlist:
    raise ValueError(
        f"{video_name} is not in the own_videos allowlist. "
        f"Allowed: {sorted(allowlist)}. Add it explicitly only if the owner recorded it."
    )
if not os.path.isfile(video_path):
    # Se sale en verde, igual que un salto por cupo: un fichero que no está no
    # es un error del pipeline y no debe tumbar la cola ni disparar el reintento
    # de la plataforma. El 2026-10-25 un pendiente fantasma murió dos veces con
    # FileNotFoundError. No se registra en bronze porque `registrar_bronze` aún
    # no existe en este punto (necesita `sha256`, que se calcula abajo).
    print(f"NO EXISTE: {video_name} no está en {VIDEOS_DIR}. Se omite.")
    dbutils.notebook.exit(f"skipped_sin_archivo:{video_name}")

video_size = os.path.getsize(video_path)
with open(video_path, "rb") as f:
    sha256 = hashlib.sha256(f.read()).hexdigest()

DRY_RUN = as_bool("dry_run")
ALLOW_RESUBMIT = as_bool("allow_resubmit")
IGNORAR_CUPO = as_bool("ignorar_cupo")


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


if not ALLOW_RESUBMIT and not DRY_RUN:
    # En dry_run no se sube nada, así que el anti-duplicados no aplica: bloquearlo
    # impedía verificar credenciales sobre cualquier archivo ya enviado.
    previous = load_previous_uploads()
    if previous:
        raise ValueError(
            "This exact file was already sent to the TikTok inbox "
            f"(publish_id={previous[0]['publish_id']} at {previous[0]['uploaded_at']}). "
            "Pass allow_resubmit=true only if the owner really wants a second draft."
        )

# COMMAND ----------

# MAGIC %md ## 2. Token: reutilizar y refrescar solo si la API dice que caducó

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

    Se escribe también cuando el inbox está lleno o no hay cupo: sin esa fila no
    habría forma de saber que el video quedó generado y pendiente de subir. Un
    fallo aquí nunca debe tumbar la tarea.
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

# MAGIC %md ## 3. Cupo del inbox: mirar antes de gastar una petición
# MAGIC
# MAGIC Se cuentan las subidas aceptadas en las últimas 24 h según bronze. Es una
# MAGIC estimación conservadora: si el dueño ya publicó un borrador, ese hueco está
# MAGIC libre en TikTok pero aquí sigue contando, así que en el peor caso se sube de
# MAGIC menos. Subir de más es lo que dispara `spam_risk_too_many_pending_share`.

# COMMAND ----------


def subidas_recientes():
    """Marcas de tiempo de las subidas aceptadas en la ventana de 24 h.

    Se consultan las DOS tablas de subidas: `tiktok_uploads` (bronze) y la
    auditoría `upload_drafts`. El 2026-10-25 bronze tenía 7 subidas en 24 h
    mientras la auditoría tenía 25: leer solo bronze hacía que el cortacircuitos
    creyera que había cupo de sobra. Se toma la unión, que es la lectura
    conservadora: como mucho se sube de menos.

    Un fallo leyendo una tabla no debe impedir subir: si ninguna responde se
    devuelve `None` para que el llamador decida.
    """
    fechas, leidas = [], 0
    for tabla, estados in ((UPLOADS_BRONZE, ESTADOS_QUE_OCUPAN),
                           (AUDIT_TABLE, ESTADOS_QUE_OCUPAN)):
        try:
            filas = spark.sql(
                f"""SELECT uploaded_at, publish_id FROM {tabla}
                    WHERE status IN {estados}
                      AND uploaded_at >= current_timestamp() - INTERVAL 24 HOURS"""
            ).collect()
            fechas.extend(filas)
            leidas += 1
        except Exception as exc:
            print(f"AVISO: no se pudo leer el cupo en {tabla} "
                  f"({type(exc).__name__}): {exc}")
    if not leidas:
        return None
    # La misma subida puede estar en las dos tablas: se deduplica por
    # `publish_id`, que es la identidad real del `init`. Sin él se cae a la marca
    # de tiempo, que es única por subida en la práctica.
    vistos, unicas = set(), []
    for fila in fechas:
        f = fila["uploaded_at"]
        if f is None:
            continue
        pid = (fila["publish_id"] or "").strip()
        clave = ("pid", pid) if pid else ("ts", cuota.como_utc(f))
        if clave not in vistos:
            vistos.add(clave)
            unicas.append(f)
    return unicas


if DRY_RUN:
    print("dry_run: se omite la comprobación de cupo.")
elif IGNORAR_CUPO:
    print("AVISO: ignorar_cupo=true; se intenta la subida aunque no haya cupo.")
else:
    fechas = subidas_recientes()
    ahora = datetime.now(timezone.utc)
    if fechas is None:
        print("AVISO: cupo desconocido; se intenta la subida igualmente.")
    elif not cuota.hay_cupo(fechas, ahora):
        # Sin cupo no se llama a la API: reintentar aquí es lo que provocó la
        # ráfaga del 2026-10-24. Se registra y se sale en verde para que el resto
        # de la cola no se detenga y el job pueda seguir con los otros canales.
        usadas = cuota.usadas_en_ventana(fechas, ahora)
        print(f"SIN CUPO: {usadas}/{cuota.LIMITE_24H} subidas en 24 h. "
              f"{video_name} no se envía.")
        registrar_bronze(
            "SKIPPED_INBOX_FULL",
            f"sin cupo: {usadas}/{cuota.LIMITE_24H} subidas en 24 h",
        )
        dbutils.notebook.exit(f"skipped_cuota:{video_name}")
    else:
        print(f"Cupo: quedan {cuota.cuantas_caben(fechas, ahora)} de {cuota.LIMITE_24H}.")

# COMMAND ----------

# MAGIC %md ## 4. Dry run: verificar credenciales sin subir nada

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

# MAGIC %md ## 5. Iniciar la subida al inbox
# MAGIC
# MAGIC Antes de gastar la petición se deja una «reserva» en bronze. El run del
# MAGIC 2026-10-25 demostró que Databricks puede repetir una iteración de `for_each`
# MAGIC hasta tres veces aunque la task tenga `max_retries: 0` (los intentos
# MAGIC aparecieron con `trigger: RETRY`). Si la repetición llega después de un
# MAGIC `init` que sí se envió, la ráfaga es exactamente lo que TikTok marca como
# MAGIC spam. La reserva hace que el segundo intento se retire sin llamar a la API.

# COMMAND ----------

REINTENTO_MIN = 30


def init_ya_intentado():
    """True si este mismo archivo ya intentó un `init` hace menos de 30 min.

    La ventana es corta a propósito: cubre los reintentos automáticos de la
    plataforma (que ocurren en segundos) sin dejar el video bloqueado para
    siempre si la subida falló por algo transitorio.
    """
    try:
        filas = spark.sql(
            f"""SELECT uploaded_at FROM {UPLOADS_BRONZE}
                WHERE video_sha256 = '{sha256}'
                  AND status = 'INICIANDO'
                  AND uploaded_at >= current_timestamp() - INTERVAL {REINTENTO_MIN} MINUTES"""
        ).collect()
    except Exception as exc:
        # Sin poder leer la reserva se sigue adelante: bloquear por una tabla
        # ilegible sería peor que el riesgo de un segundo intento.
        print(f"AVISO: no se pudo leer la reserva ({type(exc).__name__}): {exc}")
        return False
    return len(filas) > 0


if init_ya_intentado():
    print(f"REINTENTO DETENIDO: {video_name} ya intentó un init hace "
          f"<{REINTENTO_MIN} min. No se llama a la API otra vez.")
    dbutils.notebook.exit(f"skipped_reintento:{video_name}")

registrar_bronze("INICIANDO", "reserva antes de llamar a init")

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
    # La comprobación previa decía que había cupo y aun así TikTok lo rechaza: la
    # ventana del servidor no coincide con la nuestra (o el dueño no ha publicado
    # los borradores). No se reintenta; se registra y se sale en verde.
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

# MAGIC %md ## 6. Esperar a que el borrador llegue al inbox y auditarlo

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

# MAGIC %md ## 7. Avisar por Slack
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
# Se busca junto al mp4 ya resuelto (raíz o carpeta de fecha), no en la raíz
# fija: con los renders agrupados por día el `.txt` vive en esa misma carpeta.
publicacion = (rutas.companero(video_name, "_publicacion.txt", VIDEOS_DIR)
               or os.path.join(os.path.dirname(video_path),
                               os.path.splitext(video_name)[0] + "_publicacion.txt"))
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
