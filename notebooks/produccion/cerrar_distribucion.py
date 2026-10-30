# Databricks notebook source
# MAGIC %md
# MAGIC # Cerrar la distribución: resumen del día y un solo aviso
# MAGIC
# MAGIC Corre al final del DAG con `run_if: ALL_DONE`, así que se ejecuta aunque un
# MAGIC canal haya fallado. Lee `tiktok.bronze.distribucion_video` de esta corrida y
# MAGIC manda **un** mensaje de Slack con el estado de los tres canales, en vez de
# MAGIC tres avisos sueltos que no se pueden leer juntos.
# MAGIC
# MAGIC No publica nada: si un canal falló, se ve aquí y se reintenta desde la UI.

# COMMAND ----------

import json

import requests

SCOPE = "tiktok_pipeline"
DISTRIBUCION = "tiktok.bronze.distribucion_video"
CANALES = ["tiktok", "youtube", "facebook"]

# Icono por estado: el resumen se lee de un vistazo en el móvil.
EMOJI = {
    "PUBLICADO": "✅",
    "PUBLICADO_PRIVADO": "🔒",
    "EN_CURSO": "⏳",
    "PENDIENTE": "·",
    "SKIPPED_CUOTA": "⚠️",
    "SKIPPED_TOPE": "🚦",
    "SKIPPED_INBOX_FULL": "📥",
    "FALLIDO": "❌",
}

# COMMAND ----------

# MAGIC %md ## 1. ¿Qué se publicó en esta corrida?

# COMMAND ----------

run_id = ""
try:
    run_id = (dbutils.notebook.entry_point.getDbutils()
              .notebook().getContext().currentRunId().toString())
except Exception as exc:
    print(f"currentRunId no disponible ({type(exc).__name__}); se resume por tiempo.")

try:
    # El filtro por run_id es el preciso. Si no estuviera disponible, se cae a
    # las últimas 6 horas, que cubre de sobra una corrida de este job.
    filtro = (f"run_id = '{run_id}'" if run_id.isdigit()
              else "actualizado_en > current_timestamp() - INTERVAL 6 HOURS")
    filas = spark.sql(
        f"""SELECT video_name, canal, estado, id_externo, url, motivo_fallo
            FROM {DISTRIBUCION}
            WHERE {filtro}
            ORDER BY video_name, canal"""
    ).collect()
except Exception as exc:
    print(f"No se pudo leer {DISTRIBUCION} ({type(exc).__name__}): {exc}")
    dbutils.notebook.exit("sin_datos")

print(f"Filas en esta corrida: {len(filas)}")

# COMMAND ----------

# MAGIC %md ## 2. Resumen por canal

# COMMAND ----------

por_canal = {c: {"ok": 0, "privado": 0, "fallo": 0, "saltado": 0, "detalle": []} for c in CANALES}
videos = {}

for f in filas:
    canal, estado = f["canal"], f["estado"]
    videos.setdefault(f["video_name"], {})[canal] = estado

    c = por_canal.setdefault(canal, {"ok": 0, "privado": 0, "fallo": 0,
                                     "saltado": 0, "detalle": []})
    if estado == "PUBLICADO":
        c["ok"] += 1
    elif estado == "PUBLICADO_PRIVADO":
        c["privado"] += 1
    elif estado in ("SKIPPED_CUOTA", "SKIPPED_TOPE", "SKIPPED_INBOX_FULL"):
        c["saltado"] += 1
        c["detalle"].append(f"{f['video_name']}: {estado}")
    elif estado == "FALLIDO":
        c["fallo"] += 1
        c["detalle"].append(f"{f['video_name']}: {f['motivo_fallo'] or 'sin motivo'}")

# COMMAND ----------

# MAGIC %md ## 3. Mensaje único de Slack

# COMMAND ----------

def linea_canal(canal):
    c = por_canal.get(canal, {})
    partes = []
    if c.get("ok"):
        partes.append(f"{c['ok']} público(s)")
    if c.get("privado"):
        partes.append(f"{c['privado']} privado(s)")
    if c.get("saltado"):
        partes.append(f"{c['saltado']} saltado(s)")
    if c.get("fallo"):
        partes.append(f"{c['fallo']} fallido(s)")
    return " · ".join(partes) if partes else "sin actividad"


texto = "*Resumen de publicación* :bar_chart:\n"
for canal in CANALES:
    texto += f"• *{canal}*: {linea_canal(canal)}\n"

detalles = [d for canal in CANALES for d in por_canal.get(canal, {}).get("detalle", [])]
if detalles:
    texto += "\n_Incidencias:_\n" + "\n".join(f"  - {d}" for d in detalles[:10])

enlaces = [f"{f['canal']}: {f['url']}" for f in filas if f["url"]]
if enlaces:
    texto += "\n\n_Enlaces:_\n" + "\n".join(f"  - {e}" for e in enlaces[:10])

# Recordatorio del paso manual: mientras no haya auditorías, esto no es 100% auto.
if por_canal.get("tiktok", {}).get("ok") or por_canal.get("tiktok", {}).get("privado"):
    texto += "\n_Publica los borradores de TikTok desde la app._"

try:
    webhook = dbutils.secrets.get(SCOPE, "slack_webhook_videos")
    resp = requests.post(webhook, json={"text": texto}, timeout=20)
    resp.raise_for_status()
    print("Aviso enviado a Slack.")
except Exception as exc:
    print(f"No se pudo notificar a Slack ({type(exc).__name__}): {exc}")

print(texto)
dbutils.notebook.exit(json.dumps({c: linea_canal(c) for c in CANALES}, ensure_ascii=False))
