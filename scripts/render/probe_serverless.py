# Databricks notebook source
# MAGIC %md
# MAGIC # Sonda: ¿puede el serverless generar el video?
# MAGIC Comprueba Pillow, edge-tts, ffmpeg (imageio-ffmpeg) y si hay salida a internet.
# MAGIC Devuelve el resultado por `notebook.exit` porque los `print` no se capturan.

# COMMAND ----------

import asyncio
import json
import os
import subprocess
import tempfile
import traceback

resultado = {}

# COMMAND ----------

# MAGIC %md ## 1. Paquetes

# COMMAND ----------

try:
    import PIL
    resultado["pillow"] = PIL.__version__
except Exception as exc:
    resultado["pillow"] = f"ERROR {type(exc).__name__}: {exc}"

try:
    import edge_tts
    resultado["edge_tts"] = getattr(edge_tts, "__version__", "instalado sin __version__")
except Exception as exc:
    resultado["edge_tts"] = f"ERROR {type(exc).__name__}: {exc}"

try:
    import imageio_ffmpeg
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    resultado["ffmpeg_path"] = exe
    out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=60)
    resultado["ffmpeg_version"] = (out.stdout or out.stderr).splitlines()[0][:120]
except Exception as exc:
    resultado["ffmpeg_path"] = f"ERROR {type(exc).__name__}: {exc}"

# COMMAND ----------

# MAGIC %md ## 2. Voz sintética + límites de palabra (para no necesitar Whisper)

# COMMAND ----------

async def probar_tts():
    comunicador = edge_tts.Communicate("Dios te está viendo y se está riendo.", "es-MX-JorgeNeural")
    audio = bytearray()
    fronteras = []
    async for trozo in comunicador.stream():
        if trozo["type"] == "audio":
            audio.extend(trozo["data"])
        elif trozo["type"] == "WordBoundary":
            fronteras.append({
                "texto": trozo["text"],
                "inicio": round(trozo["offset"] / 1e7, 2),
                "fin": round((trozo["offset"] + trozo["duration"]) / 1e7, 2),
            })
    return bytes(audio), fronteras


try:
    datos, fronteras = asyncio.run(probar_tts())
    ruta = os.path.join(tempfile.gettempdir(), "probe_tts.mp3")
    with open(ruta, "wb") as f:
        f.write(datos)
    resultado["tts_bytes"] = len(datos)
    resultado["tts_palabras"] = len(fronteras)
    resultado["tts_muestra"] = fronteras[:4]
except Exception as exc:
    resultado["tts_bytes"] = f"ERROR {type(exc).__name__}: {exc}"
    resultado["tts_traza"] = traceback.format_exc()[-600:]

# COMMAND ----------

# MAGIC %md ## 3. Escritura en el volumen

# COMMAND ----------

try:
    ruta_vol = "/Volumes/tiktok/default/videos/_probe_serverless.txt"
    with open(ruta_vol, "w") as f:
        f.write("ok")
    os.remove(ruta_vol)
    resultado["volumen"] = "escritura ok"
except Exception as exc:
    resultado["volumen"] = f"ERROR {type(exc).__name__}: {exc}"

# COMMAND ----------

resultado["python"] = __import__("sys").version.split()[0]
dbutils.notebook.exit(json.dumps(resultado, ensure_ascii=False, indent=2))
