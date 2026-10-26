# Databricks notebook source
# MAGIC %md
# MAGIC # Sonda 2: TTS con `await` de nivel superior e import desde el volumen
# MAGIC La sonda 1 falló solo porque el kernel ya tiene un event loop. Aquí se
# MAGIC confirma si edge-tts funciona y si el serverless importa `.py` del volumen.

# COMMAND ----------

import json
import os
import sys
import traceback

resultado = {}

# COMMAND ----------

# MAGIC %md ## 1. Voz sintética con límites de palabra

# COMMAND ----------

import edge_tts

try:
    comunicador = edge_tts.Communicate(
        "Dios te está viendo y se está riendo. Comparte esto con quien lo necesite.",
        "es-MX-JorgeNeural",
    )
    audio = bytearray()
    fronteras = []
    async for trozo in comunicador.stream():
        if trozo["type"] == "audio":
            audio.extend(trozo["data"])
        elif trozo["type"] == "WordBoundary":
            fronteras.append({
                "texto": trozo["text"],
                "inicio": round(trozo["offset"] / 1e7, 3),
                "fin": round((trozo["offset"] + trozo["duration"]) / 1e7, 3),
            })

    ruta = os.path.join("/tmp", "probe2.mp3")
    with open(ruta, "wb") as f:
        f.write(audio)

    resultado["tts_bytes"] = len(audio)
    resultado["tts_palabras"] = len(fronteras)
    resultado["tts_ultima"] = fronteras[-1] if fronteras else None
except Exception as exc:
    resultado["tts"] = f"ERROR {type(exc).__name__}: {exc}"
    resultado["tts_traza"] = traceback.format_exc()[-800:]

# COMMAND ----------

# MAGIC %md ## 2. Import de un `.py` alojado en el volumen

# COMMAND ----------

LIB = "/Volumes/tiktok/default/videos/lib"
try:
    sys.path.insert(0, LIB)
    import lib_probe
    resultado["import_volumen"] = lib_probe.saludo()
except Exception as exc:
    resultado["import_volumen"] = f"ERROR {type(exc).__name__}: {exc}"

# COMMAND ----------

dbutils.notebook.exit(json.dumps(resultado, ensure_ascii=False, indent=2))
