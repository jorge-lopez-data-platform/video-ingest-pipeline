# Databricks notebook source
# MAGIC %md
# MAGIC # Upload one of the owner's own videos to TikTok (manual, SELF_ONLY)
# MAGIC Source file must be placed by the owner in `/Volumes/tiktok/default/videos/`.
# MAGIC Requires scopes `video.upload` and `video.publish` on the token in `tiktok_pipeline/tiktok_access_token`.

# COMMAND ----------

import os
import time

import requests

dbutils.widgets.text("video_path", "")
dbutils.widgets.text("caption", "")
dbutils.widgets.dropdown("privacy_level", "PUBLIC_TO_EVERYONE", ["PUBLIC_TO_EVERYONE", "SELF_ONLY"])

VIDEO_PATH = dbutils.widgets.get("video_path").strip()
CAPTION = dbutils.widgets.get("caption").strip()
PRIVACY = dbutils.widgets.get("privacy_level")

if not VIDEO_PATH.startswith("/Volumes/tiktok/default/videos/") or not VIDEO_PATH.endswith(".mp4"):
    raise ValueError("video_path must be an .mp4 inside /Volumes/tiktok/default/videos/")
if not os.path.isfile(VIDEO_PATH):
    raise FileNotFoundError(VIDEO_PATH)

TOKEN = dbutils.secrets.get("tiktok_pipeline", "tiktok_access_token")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json; charset=UTF-8"}

# COMMAND ----------

video_size = os.path.getsize(VIDEO_PATH)

init = requests.post(
    "https://open.tiktokapis.com/v2/post/publish/video/init/",
    headers=HEADERS,
    json={
        "post_info": {
            "title": CAPTION[:150],
            "privacy_level": PRIVACY,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1,
        },
    },
    timeout=60,
).json()

if init.get("error", {}).get("code") != "ok":
    raise RuntimeError(f"Init failed: {init.get('error')}")
publish_id = init["data"]["publish_id"]
upload_url = init["data"]["upload_url"]

# COMMAND ----------

with open(VIDEO_PATH, "rb") as f:
    data = f.read()

put = requests.put(
    upload_url,
    headers={"Content-Range": f"bytes 0-{video_size - 1}/{video_size}", "Content-Type": "video/mp4"},
    data=data,
    timeout=600,
)
put.raise_for_status()

# COMMAND ----------

status = None
for _ in range(20):
    resp = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
        headers=HEADERS,
        json={"publish_id": publish_id},
        timeout=60,
    ).json()
    status = resp.get("data", {}).get("status")
    print("Status:", status)
    if status in ("PUBLISH_COMPLETE", "FAILED"):
        break
    time.sleep(10)

print("publish_id:", publish_id, "| final status:", status)
if status != "PUBLISH_COMPLETE":
    raise RuntimeError(f"Publish did not complete: {status}")
