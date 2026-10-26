# Databricks notebook source
# MAGIC %md
# MAGIC # TikTok OAuth (Login Kit) + read-only account metrics
# MAGIC Modes: `exchange` (code -> tokens), `refresh` (renew access token), `fetch` (show profile stats and videos).
# MAGIC Credentials live in secret scope `tiktok_pipeline`. Tokens are never printed.

# COMMAND ----------

import json

import requests
from databricks.sdk import WorkspaceClient

dbutils.widgets.dropdown("mode", "exchange", ["exchange", "refresh", "fetch"])
dbutils.widgets.text("code", "")
dbutils.widgets.text("redirect_uri", "https://mck2775.github.io/lomasviral/callback.html")

MODE = dbutils.widgets.get("mode")
SCOPE = "tiktok_pipeline"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2"

w = WorkspaceClient()

def secret(key):
    return dbutils.secrets.get(SCOPE, key)

def save_tokens(payload):
    w.secrets.put_secret(scope=SCOPE, key="tiktok_access_token", string_value=payload["access_token"])
    if payload.get("refresh_token"):
        w.secrets.put_secret(scope=SCOPE, key="tiktok_refresh_token", string_value=payload["refresh_token"])
    print("Saved tokens to secret scope. open_id:", payload.get("open_id"))
    print("Granted scopes:", payload.get("scope"))
    print("Access token valid (s):", payload.get("expires_in"), "| refresh token valid (s):", payload.get("refresh_expires_in"))

def token_request(extra):
    body = {"client_key": secret("tiktok_client_key"), "client_secret": secret("tiktok_client_secret"), **extra}
    r = requests.post(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
    payload = r.json()
    if r.status_code != 200 or "access_token" not in payload:
        raise RuntimeError(f"TikTok token endpoint error ({r.status_code}): {payload.get('error')} - {payload.get('error_description')}")
    return payload

# COMMAND ----------

if MODE == "exchange":
    code = dbutils.widgets.get("code").strip()
    if not code:
        raise ValueError("Paste the authorization code from callback.html into the `code` widget")
    save_tokens(token_request({
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": dbutils.widgets.get("redirect_uri"),
    }))

elif MODE == "refresh":
    save_tokens(token_request({
        "grant_type": "refresh_token",
        "refresh_token": secret("tiktok_refresh_token"),
    }))

elif MODE == "fetch":
    headers = {"Authorization": f"Bearer {secret('tiktok_access_token')}"}

    info = requests.get(
        f"{API}/user/info/",
        params={"fields": "open_id,display_name,bio_description,is_verified,follower_count,following_count,likes_count,video_count"},
        headers=headers, timeout=60,
    ).json()
    if info.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"user/info failed: {info.get('error')}")
    print("Account:")
    print(json.dumps(info["data"]["user"], indent=2, ensure_ascii=False))

    vids = requests.post(
        f"{API}/video/list/",
        params={"fields": "id,title,create_time,view_count,like_count,comment_count,share_count"},
        headers={**headers, "Content-Type": "application/json"},
        json={"max_count": 20}, timeout=60,
    ).json()
    if vids.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"video/list failed: {vids.get('error')}")
    videos = vids["data"].get("videos", [])
    print(f"\nPublic videos returned: {len(videos)}")
    if videos:
        display(spark.createDataFrame(videos))
