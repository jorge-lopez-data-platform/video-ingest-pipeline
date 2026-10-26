# Databricks notebook source
# MAGIC %md
# MAGIC # Diagnostico: publicacion directa (Direct Post)
# MAGIC
# MAGIC Refresca el token (igual que los jobs), imprime los scopes concedidos y
# MAGIC consulta `creator_info/query`, que es obligatorio antes de un Direct Post.
# MAGIC No sube ni publica nada.

# COMMAND ----------

import requests
from databricks.sdk import WorkspaceClient

SCOPE = "tiktok_pipeline"
API = "https://open.tiktokapis.com/v2"
TOKEN_URL = f"{API}/oauth/token/"

w = WorkspaceClient()


def secret(key):
    return dbutils.secrets.get(SCOPE, key)


# COMMAND ----------

payload = requests.post(
    TOKEN_URL,
    data={
        "client_key": secret("tiktok_client_key"),
        "client_secret": secret("tiktok_client_secret"),
        "grant_type": "refresh_token",
        "refresh_token": secret("tiktok_refresh_token"),
    },
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    timeout=60,
).json()

if "access_token" not in payload:
    raise RuntimeError(f"refresh failed: {payload.get('error')} - {payload.get('error_description')}")

print("GRANTED SCOPES:", payload.get("scope"))
print("access expires_in:", payload.get("expires_in"))
print("refresh expires_in:", payload.get("refresh_expires_in"))

# Persistir los tokens rotados, igual que oauth_tiktok.py
w.secrets.put_secret(scope=SCOPE, key="tiktok_access_token", string_value=payload["access_token"])
if payload.get("refresh_token"):
    w.secrets.put_secret(scope=SCOPE, key="tiktok_refresh_token", string_value=payload["refresh_token"])
print("Tokens guardados en el scope.")

HEADERS = {"Authorization": f"Bearer {payload['access_token']}", "Content-Type": "application/json; charset=UTF-8"}

# COMMAND ----------

# MAGIC %md ## Creator info (requerido por Direct Post)

# COMMAND ----------

info = requests.post(f"{API}/post/publish/creator_info/query/", headers=HEADERS, json={}, timeout=60).json()

print("error:", info.get("error"))
data = info.get("data") or {}
for k in (
    "creator_username",
    "privacy_level_options",
    "comment_disabled",
    "duet_disabled",
    "stitch_disabled",
    "max_video_post_duration_sec",
):
    if k in data:
        print(f"{k}: {data[k]}")

# COMMAND ----------

# MAGIC %md ## Sonda de scope: init de Direct Post con payload minimo invalido
# MAGIC
# MAGIC Si falta `video.publish` el error sera `scope_not_authorized`. Si el scope
# MAGIC existe pero el payload es invalido, el error sera distinto (validacion),
# MAGIC que es lo que queremos ver.

# COMMAND ----------

probe = requests.post(
    f"{API}/post/publish/video/init/",
    headers=HEADERS,
    json={
        "post_info": {"title": "probe", "privacy_level": "SELF_ONLY"},
        "source_info": {"source": "FILE_UPLOAD", "video_size": 1000000, "chunk_size": 1000000, "total_chunk_count": 1},
    },
    timeout=60,
).json()

print("PROBE:", probe.get("error"))
dbutils.notebook.exit(str({"scope": payload.get("scope"), "probe": probe.get("error")}))
