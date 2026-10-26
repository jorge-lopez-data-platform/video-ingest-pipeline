# Databricks notebook source
# MAGIC %md
# MAGIC # Empaqueta el `descripciones_reels.md` del lote
# MAGIC
# MAGIC Un solo `.md` junto a los mp4 del día, con título, descripción, tags y
# MAGIC hashtags de cada video, listos para pegar en el formulario de YouTube.
# MAGIC
# MAGIC Existe como tarea aparte porque el archivo es **uno por lote** y se
# MAGIC sobrescribe: si lo escribiera cada iteración del render paralelo, la última
# MAGIC en terminar ganaría y el documento acabaría describiendo un solo video.
# MAGIC Por eso se genera después de `generar_video`, desde el manifiesto, que ya
# MAGIC tiene el guion y los hashtags de cada video.
# MAGIC
# MAGIC Se escribe aunque el lote tenga un solo video: así la carpeta del día nunca
# MAGIC queda con videos y sin paquete.

# COMMAND ----------

import json
import sys
from pathlib import Path

dbutils.widgets.text("manifiesto", "", "Ruta del manifiesto del lote (task value)")

LIB = "/Volumes/tiktok/default/videos/lib"
VIDEOS_DIR = "/Volumes/tiktok/default/videos"

sys.path.insert(0, LIB)
from scripts import descripciones, rutas, titulos  # noqa: E402

print("Librería importada desde", LIB)

# COMMAND ----------

ruta_manifiesto = dbutils.widgets.get("manifiesto").strip()
if not ruta_manifiesto or not Path(ruta_manifiesto).is_file():
    raise FileNotFoundError(f"No existe el manifiesto {ruta_manifiesto!r}")

lote = json.loads(Path(ruta_manifiesto).read_text(encoding="utf-8"))

# Solo se documenta lo que de verdad se renderizó: el manifiesto lista el lote
# planeado, y un render puede fallar. Se comprueba el disco en vez de fiarse del
# manifiesto, porque el disco es la única prueba de que el mp4 existe.
carpeta_dia = Path(rutas.carpeta_del_dia(VIDEOS_DIR))
videos = []
for entrada in lote["videos"]:
    vid = str(entrada["video_fuente_id"])
    if not (carpeta_dia / entrada["video_name"]).is_file():
        print(f"  se omite {vid}: {entrada['video_name']} no está en {carpeta_dia}")
        continue
    guion = entrada["guion"]
    # El título del manifiesto es el que se quemó en el video y el que se
    # publicó; cogerlo del guion documentaría un titular que no está en ninguna
    # parte. Los manifiestos viejos (sin el campo) caen al de siempre.
    titulo = (entrada.get("titulo") or "").strip() or titulos.normalizar(
        guion.get("texto_pantalla_1") or guion.get("hook"))
    videos.append({
        "video_name": entrada["video_name"],
        "titulo": titulo,
        "hashtags": entrada.get("hashtags") or "",
        "guion": guion,
    })

if not videos:
    raise RuntimeError(f"No hay videos que documentar en {carpeta_dia}: "
                       "el lote no generó ningún mp4.")

paquete = descripciones.escribir(videos, str(carpeta_dia))
print(f"📄 {paquete} ({len(videos)} videos)")
