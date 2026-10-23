import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://open.tiktokapis.com/v2"
TOKEN = os.environ["TIKTOK_ACCESS_TOKEN"]
VIDEO = sys.argv[1]
TITLE = sys.argv[2]


def call(method, url, body=None):
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json; charset=UTF-8"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


info = call("POST", f"{API}/post/publish/creator_info/query/")
assert info.get("error", {}).get("code") == "ok", f"creator_info falló: {info}"
options = info["data"]["privacy_level_options"]
privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options else "SELF_ONLY"
print("Privacidad usada:", privacy, "| opciones:", options)

size = os.path.getsize(VIDEO)
init = call("POST", f"{API}/post/publish/video/init/", {
    "post_info": {
        "title": TITLE,
        "privacy_level": privacy,
        "disable_duet": False,
        "disable_comment": False,
        "disable_stitch": False,
    },
    "source_info": {
        "source": "FILE_UPLOAD",
        "video_size": size,
        "chunk_size": size,
        "total_chunk_count": 1,
    },
})
assert init.get("error", {}).get("code") == "ok", f"Init falló: {init}"
upload_url = init["data"]["upload_url"]
publish_id = init["data"]["publish_id"]

with open(VIDEO, "rb") as f:
    payload = f.read()
req = urllib.request.Request(upload_url, data=payload, method="PUT", headers={
    "Content-Type": "video/mp4",
    "Content-Length": str(size),
    "Content-Range": f"bytes 0-{size - 1}/{size}",
})
with urllib.request.urlopen(req, timeout=300) as r:
    print("PUT:", r.status)

for _ in range(30):
    st = call("POST", f"{API}/post/publish/status/fetch/", {"publish_id": publish_id})
    status = st.get("data", {}).get("status")
    print("Estado:", status, st.get("data", {}).get("fail_reason", ""))
    if status in ("PUBLISH_COMPLETE", "FAILED"):
        break
    time.sleep(5)
