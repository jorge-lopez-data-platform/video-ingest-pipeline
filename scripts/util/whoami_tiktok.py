# Databricks notebook source
# MAGIC %md
# MAGIC # Consulta de solo lectura: a qué cuenta pertenece el token de TikTok
# MAGIC Devuelve el perfil por `notebook.exit` porque los `print` no se capturan
# MAGIC en serverless y sin esto no hay forma de leer el resultado desde el CLI.

# COMMAND ----------

import json

import requests

TOKEN = dbutils.secrets.get("tiktok_pipeline", "tiktok_access_token")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

info = requests.get(
    "https://open.tiktokapis.com/v2/user/info/",
    params={"fields": "open_id,display_name,username,bio_description,follower_count,video_count"},
    headers=HEADERS,
    timeout=60,
).json()

if info.get("error", {}).get("code") != "ok":
    dbutils.notebook.exit(json.dumps({"error": info.get("error")}))

u = info["data"]["user"]
dbutils.notebook.exit(json.dumps({
    "username": u.get("username"),
    "display_name": u.get("display_name"),
    "open_id": u.get("open_id"),
    "followers": u.get("follower_count"),
    "videos": u.get("video_count"),
}, ensure_ascii=False))
