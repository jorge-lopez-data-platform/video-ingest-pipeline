# Databricks notebook source
# MAGIC %pip install -q yt-dlp curl_cffi

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import re
from yt_dlp import YoutubeDL

dbutils.widgets.text("urls", "", "URLs de TikTok (separadas por coma o salto de linea)")
dbutils.widgets.text("own_handles", "", "Cuentas propias permitidas (separadas por coma, sin @)")
dbutils.widgets.text("output_dir", "/Volumes/tiktok/default/videos/own", "Carpeta de salida (Volume)")

urls = [u.strip() for u in re.split(r"[,\n]", dbutils.widgets.get("urls")) if u.strip()]
own_handles = {h.strip().lstrip("@").lower() for h in dbutils.widgets.get("own_handles").split(",") if h.strip()}
output_dir = dbutils.widgets.get("output_dir").rstrip("/")

if not urls:
    raise ValueError("Parametro 'urls' vacio: indica al menos una URL de TikTok.")
if not own_handles:
    raise ValueError("Parametro 'own_handles' vacio: se requiere al menos una cuenta propia.")

os.makedirs(output_dir, exist_ok=True)

# COMMAND ----------

BASE_OPTS = {
    "format": "best",
    "noplaylist": True,
    "impersonate": "chrome",  # TikTok requiere impersonacion (curl_cffi)
    "quiet": True,
    "no_warnings": True,
}


def descargar_video_propio(url):
    with YoutubeDL(BASE_OPTS) as ydl:
        info = ydl.extract_info(url, download=False)

    autor = (info.get("uploader_id") or info.get("uploader") or "").lstrip("@").lower()
    if autor not in own_handles:
        return {"url": url, "status": "skipped", "detail": f"autor '{autor}' no esta en own_handles"}

    opts = {**BASE_OPTS, "outtmpl": f"{output_dir}/%(uploader_id)s_%(id)s.%(ext)s"}
    with YoutubeDL(opts) as ydl:
        ydl.download([url])
    return {"url": url, "status": "ok", "detail": f"{autor}_{info['id']}"}


resultados = []
for u in urls:
    try:
        r = descargar_video_propio(u)
    except Exception as e:
        r = {"url": u, "status": "error", "detail": str(e)[:300]}
    print(r)
    resultados.append(r)

# COMMAND ----------

fallidos = [r for r in resultados if r["status"] == "error"]
omitidos = [r for r in resultados if r["status"] == "skipped"]
print(f"ok={sum(r['status']=='ok' for r in resultados)} omitidos={len(omitidos)} errores={len(fallidos)}")
if fallidos or omitidos:
    raise RuntimeError(f"Hubo URLs omitidas o con error: {fallidos + omitidos}")
