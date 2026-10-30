# Databricks notebook source
# MAGIC %md
# MAGIC # Subir borradores: cola y subida en un solo paso
# MAGIC
# MAGIC Fusiona `pendientes_borradores` y el for_each de `upload_draft_video`.
# MAGIC La cola son los videos generados (`gold.guiones_usados`) menos los ya enviados
# MAGIC (`bronze.tiktok_uploads`). Cada video se sube llamando a `upload_draft_video`
# MAGIC en secuencia. Sin reintentos: los errores de subida son deterministas.
# MAGIC Si el inbox está lleno se detiene; lo que queda sigue en la cola de la siguiente corrida.

# COMMAND ----------

import json
import os

dbutils.widgets.text("max_pendientes", "2", "Tope de videos a subir por corrida (0 = todos)")
dbutils.widgets.text("allow_resubmit", "false", "Permitir reenviar un archivo ya enviado")

GENERADOS = "tiktok.gold.guiones_usados"
SUBIDOS = "tiktok.bronze.tiktok_uploads"
VIDEOS_DIR = "/Volumes/tiktok/default/videos"
UPLOAD_NB = "/Users/USUARIO/tiktok_ingest/upload_draft_video"
ALLOW_RESUBMIT = dbutils.widgets.get("allow_resubmit").strip().lower()

try:
    MAX_PENDIENTES = max(0, int(dbutils.widgets.get("max_pendientes") or 0))
except ValueError:
    MAX_PENDIENTES = 2
    print("max_pendientes invalido; se usa 2.")

# COMMAND ----------

pendientes = spark.sql(
    f"""
    SELECT g.video_name
    FROM {GENERADOS} g
    LEFT JOIN (
        SELECT DISTINCT video_name
        FROM {SUBIDOS}
        WHERE status IN ('SEND_TO_USER_INBOX', 'PUBLISH_COMPLETE')
    ) s ON s.video_name = g.video_name
    WHERE s.video_name IS NULL
    ORDER BY g.generado_en
    """
).collect()

cola = []
for fila in pendientes:
    nombre = fila["video_name"]
    if os.path.isfile(f"{VIDEOS_DIR}/{nombre}"):
        cola.append(nombre)
    else:
        print(f"AVISO: {nombre} ya no está en el volumen; se ignora.")

total = len(cola)
if MAX_PENDIENTES and total > MAX_PENDIENTES:
    print(f"AVISO: {total} pendientes; se suben solo {MAX_PENDIENTES} en esta corrida.")
    cola = cola[:MAX_PENDIENTES]
print(f"Pendientes de subir: {len(cola)} de {total} totales")

# COMMAND ----------

resultados = {}
for nombre in cola:
    try:
        resultado = dbutils.notebook.run(
            UPLOAD_NB,
            1500,
            {"video_path": nombre, "own_videos": nombre,
             "allow_resubmit": ALLOW_RESUBMIT, "dry_run": "false"},
        )
    except Exception as exc:
        resultado = f"error:{type(exc).__name__}"
    print(f"  {nombre} -> {resultado}")
    resultados[nombre] = resultado
    if resultado.startswith("skipped_inbox_full"):
        print("Inbox lleno: se detiene; el resto queda para la siguiente corrida.")
        break

# COMMAND ----------

dbutils.notebook.exit(json.dumps(resultados, ensure_ascii=False))
