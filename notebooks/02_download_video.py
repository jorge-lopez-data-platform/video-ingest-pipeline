# Databricks notebook source
import requests

from urllib.parse import urlparse

video_url = dbutils.jobs.taskValues.get(taskKey="scrape_viral_videos", key="video_url")
video_id = dbutils.jobs.taskValues.get(taskKey="scrape_viral_videos", key="video_id")

local_path = f"/Volumes/tiktok/default/videos/{video_id}.mp4"

headers = {}
if urlparse(video_url).hostname == "api.apify.com":
    headers["Authorization"] = f"Bearer {dbutils.secrets.get('tiktok_pipeline', 'apify_token')}"

r = requests.get(video_url, headers=headers, stream=True, timeout=300)
r.raise_for_status()
with open(local_path, "wb") as f:
    for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
        f.write(chunk)

print(f"Guardado en: {local_path}")
dbutils.jobs.taskValues.set(key="local_video_path", value=local_path)
