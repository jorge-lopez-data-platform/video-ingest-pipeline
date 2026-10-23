# Databricks notebook source
import os, time, requests

TIKTOK_TOKEN = dbutils.secrets.get("tiktok_pipeline", "tiktok_access_token")
local_video_path = dbutils.jobs.taskValues.get(taskKey="download_video", key="local_video_path")
caption = dbutils.jobs.taskValues.get(taskKey="scrape_viral_videos", key="caption")

video_size = os.path.getsize(local_video_path)

init_resp = requests.post(
    "https://open.tiktokapis.com/v2/post/publish/video/init/",
    headers={
        "Authorization": f"Bearer {TIKTOK_TOKEN}",
        "Content-Type": "application/json; charset=UTF-8",
    },
    json={
        "post_info": {
            "title": caption,
            "privacy_level": "SELF_ONLY",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000,
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

assert "data" in init_resp and init_resp["data"].get("publish_id"), f"Init falló: {init_resp}"
publish_id = init_resp["data"]["publish_id"]
upload_url = init_resp["data"]["upload_url"]

with open(local_video_path, "rb") as f:
    data = f.read()

put_resp = requests.put(
    upload_url,
    headers={"Content-Range": f"bytes 0-{video_size - 1}/{video_size}", "Content-Type": "video/mp4"},
    data=data,
    timeout=600,
)
put_resp.raise_for_status()

for _ in range(12):
    status_resp = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
        headers={"Authorization": f"Bearer {TIKTOK_TOKEN}", "Content-Type": "application/json; charset=UTF-8"},
        json={"publish_id": publish_id},
        timeout=60,
    ).json()
    status = status_resp["data"]["status"]
    print("Status:", status)
    if status in ("PUBLISH_COMPLETE", "FAILED"):
        break
    time.sleep(5)
