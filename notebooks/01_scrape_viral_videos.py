# Databricks notebook source
import requests

APIFY_TOKEN = dbutils.secrets.get("tiktok_pipeline", "apify_token")
ACTOR_ID = "clockworks~tiktok-scraper"

run_input = {
    "hashtags": ["fyp", "viral"],
    "resultsPerPage": 50,
    "shouldDownloadVideos": True,
}

resp = requests.post(
    f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items",
    headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
    json=run_input,
    timeout=600,
)
resp.raise_for_status()
videos = resp.json()
assert videos, "El actor no devolvió videos"

videos_sorted = sorted(videos, key=lambda v: v.get("playCount", 0), reverse=True)
top_video = videos_sorted[0]

media_urls = top_video.get("mediaUrls") or []
video_download_url = media_urls[0] if media_urls else top_video.get("videoMeta", {}).get("downloadAddr")
assert video_download_url, f"Sin URL de descarga. Campos disponibles: {list(top_video.keys())}"

video_id = str(top_video["id"])
caption = (top_video.get("text") or "")[:150]

print(f"Video más viral: {video_id} | {top_video.get('playCount')} plays")

dbutils.jobs.taskValues.set(key="video_url", value=video_download_url)
dbutils.jobs.taskValues.set(key="video_id", value=video_id)
dbutils.jobs.taskValues.set(key="caption", value=caption)
