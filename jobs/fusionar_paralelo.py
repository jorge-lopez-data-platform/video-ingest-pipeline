"""Fusiona la nueva estructura paralela sobre el job VIVO (no sobre el repo).

Reconstruir desde una copia guardada en disco es peligroso: cualquier tarea que
se añada al job despues de exportar esa copia se pierde en el `jobs reset` sin
que nadie lo note. Ya paso una vez: la copia tenia 7 tareas y el job vivo 8
(faltaba `publicar_tiktok_directo`). Por eso este script SIEMPRE pregunta al
API por el estado actual, y la copia en disco solo sirve de respaldo.

Uso:  python jobs/fusionar_paralelo.py
Lee:  el job vivo por `databricks jobs get`
Escribe: job_produccion_paralelo.json
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
JOB_ID = 813880207704409
PERFIL = "DEFAULT"
SALIDA = RAIZ / "job_produccion_paralelo.json"

NB = "/Users/lopezechagarayjorge@gmail.com/tiktok_ingest"
SLACK = [{"id": "477cffb7-1b7c-4893-b0ca-ab7bbaf989d8"}]

# El job se lee en cada ejecucion. `--profile` explicito siempre: nunca se
# deja que el CLI elija por su cuenta.
crudo = subprocess.run(
    ["databricks", "jobs", "get", str(JOB_ID), "--profile", PERFIL, "--output", "json"],
    capture_output=True, text=True, check=False,
)
if crudo.returncode != 0:
    sys.exit(f"No se pudo leer el job {JOB_ID}: {crudo.stderr.strip()}")

vivo = json.loads(crudo.stdout)
settings = copy.deepcopy(vivo["settings"])

# `jobs get` devuelve campos de solo lectura que `jobs reset` rechaza.
# `format` se cae aunque no sea de solo lectura: el reset lo rechaza y no hace
# falta, porque la forma del propio `settings` ya define el job como multi-tarea.
for campo in ("job_id", "created_time", "creator_user_name", "run_as_user_name",
              "effective_budget_policy_id", "has_more", "next_page_token", "format"):
    settings.pop(campo, None)

tareas = {t["task_key"]: t for t in settings["tasks"]}
params = {p["name"]: p for p in settings.get("parameters", [])}

# --- parámetros nuevos que necesitan los notebooks del lote ---
nuevos = {
    "llm_model": "deepseek/deepseek-v4-pro-0813",
    "regenerar_ids": "",
    "desde_historial": "false",
    "reusar_personaje": "false",
    "sufijo": "",
    "dry_run": "false",
}
for nombre, defecto in nuevos.items():
    params.setdefault(nombre, {"name": nombre, "default": defecto})
settings["parameters"] = list(params.values())

# --- 1. seleccionar_guiones: la fase serial, antes del render ---
seleccionar = {
    "task_key": "seleccionar_guiones",
    "description": (
        "Fase serial: elige los guiones, disena las fichas del personaje y sortea "
        "los hashtags UNA vez para todo el lote, y deja el manifiesto que consume "
        "el for_each. Todo lo que depende del lote entero vive aqui porque dentro "
        "de N iteraciones aisladas no se puede mantener."
    ),
    "depends_on": [{"task_key": "guiones_60s"}],
    "environment_key": "video",
    "notebook_task": {
        "notebook_path": f"{NB}/seleccionar_guiones",
        "base_parameters": {
            "cantidad": "{{job.parameters.cantidad}}",
            "video_fuente_id": "{{job.parameters.video_fuente_id}}",
            "excluir_ids": "{{job.parameters.excluir_ids}}",
            "regenerar_ids": "{{job.parameters.regenerar_ids}}",
            "personaje": "{{job.parameters.personaje}}",
            "llm_model": "{{job.parameters.llm_model}}",
            "temas_hashtags": "{{job.parameters.hashtags}}",
            "n_hashtags": "{{job.parameters.n_hashtags}}",
            "voz": "{{job.parameters.voz}}",
            "rate": "{{job.parameters.rate}}",
            "marca_usuario": "{{job.parameters.marca_usuario}}",
            "reusar_personaje": "{{job.parameters.reusar_personaje}}",
            "sufijo": "{{job.parameters.sufijo}}",
            "desde_historial": "{{job.parameters.desde_historial}}",
        },
    },
    "webhook_notifications": {
        "on_start": SLACK, "on_success": SLACK, "on_failure": SLACK,
    },
    "timeout_seconds": 900,
}

# --- 2. generar_video pasa a for_each_task con concurrencia ---
generar = tareas["generar_video"]
generar["depends_on"] = [{"task_key": "seleccionar_guiones"}]
generar.pop("notebook_task", None)
generar.pop("environment_key", None)
# Una tarea for_each no admite timeout en el padre: el API lo rechaza con
# "it has timeout configured. For each task cannot be timed out." El límite va
# en la tarea iterada (`for_each_task.task.timeout_seconds`), que aquí son 2400 s.
generar.pop("timeout_seconds", None)
generar["description"] = (
    "for_each_task: una iteracion por guion, hasta concurrency a la vez. Cada "
    "iteracion tiene su propio compute, que es lo que paraleliza de verdad (el "
    "render es CPU puro: Pillow + dos encodes x264)."
)
generar["for_each_task"] = {
    "inputs": "{{tasks.seleccionar_guiones.values.ids}}",
    "concurrency": 3,
    "task": {
        "task_key": "render_video",
        "max_retries": 1,
        "environment_key": "video",
        "notebook_task": {
            "notebook_path": f"{NB}/render_video_lote",
            "base_parameters": {
                "manifiesto": "{{tasks.seleccionar_guiones.values.manifiesto}}",
                # El parámetro NO puede llamarse `video_fuente_id`: el job tiene
                # un job parameter con ese nombre, y cuando el base_parameter de
                # la iteración se llama igual que uno del job, Databricks
                # resuelve contra el del job (default "") y `{{input}}` llega
                # vacío. Verificado con un job A/B el 2026-10-25.
                "id_lote": "{{input}}",
                "dry_run": "{{job.parameters.dry_run}}",
            },
        },
        "webhook_notifications": {"on_failure": SLACK},
        "timeout_seconds": 2400,
    },
}

# --- 3. empaquetar: el descripciones_reels.md del lote ---
empaquetar = {
    "task_key": "empaquetar",
    "description": (
        "Escribe el descripciones_reels.md del lote. Va aqui y no en el render "
        "porque ese archivo es UNO por lote y se sobrescribe: si lo escribiera "
        "cada iteracion paralela, la ultima en terminar ganaria y solo "
        "documentaria su video."
    ),
    "depends_on": [{"task_key": "generar_video"}],
    "run_if": "ALL_DONE",
    "environment_key": "publish",
    "notebook_task": {
        "notebook_path": f"{NB}/empaquetar_lote",
        "base_parameters": {
            "manifiesto": "{{tasks.seleccionar_guiones.values.manifiesto}}",
        },
    },
    "webhook_notifications": {"on_failure": SLACK},
    "timeout_seconds": 900,
}

# --- 4. las tareas que leen los videos generados apuntan ahora a
#        seleccionar_guiones: `generar_video` es for_each y NO expone al padre
#        los task values de sus iteraciones. ---
# `publicar_tiktok_directo` entra en la lista aunque este desactivada: sigue
# existiendo en el job y apuntaba a `generar_video.values.videos`. Hoy no corre,
# pero si algun dia se reactiva sin este arreglo no tendria nada que publicar.
for clave in ("publicar_youtube", "publicar_facebook", "publicar_tiktok_directo"):
    tarea = tareas.get(clave)
    if tarea is None or "for_each_task" not in tarea:
        continue
    fe = tarea["for_each_task"]
    antes = fe["inputs"]
    # Lista PLANA de nombres + `{{input}}`: sobre una lista de diccionarios con
    # `{{input.video_name}}` la referencia llegaba nula y las iteraciones morian
    # con TypeError. Verificado en el job de prueba.
    fe["inputs"] = "{{tasks.seleccionar_guiones.values.nombres}}"
    param = fe["task"]["notebook_task"]["base_parameters"]
    for k, v in list(param.items()):
        if v in ("{{input.video_name}}", "{{input.video_fuente_id}}"):
            param[k] = "{{input}}"
    print(f"{clave}: inputs {antes} -> {fe['inputs']}")

# `pendientes_borradores` lee el task value `videos` de la tarea que lo produce.
# Se actualiza el notebook para que lo lea de `seleccionar_guiones`; el DAG no
# cambia aqui. `subir_borradores` sigue colgando de `listar_pendientes` a
# proposito: esa tarea arma la cola COMPLETA (los nuevos + los que quedaron
# atrasados de corridas anteriores), y engancharlo directo al render dejaria los
# pendientes viejos sin drenar para siempre.

# --- 5. orden final del DAG ---
# El orden es solo estetico (lo que se ve en la UI); la ejecucion la manda
# `depends_on`. Se listan las conocidas primero y **cualquier tarea viva que no
# este en la lista se conserva al final**: perder una tarea por no haberla
# previsto es mucho peor que verla fuera de orden.
tareas["seleccionar_guiones"] = seleccionar
tareas["empaquetar"] = empaquetar

orden = ["guiones_60s", "seleccionar_guiones", "generar_video", "empaquetar",
         "listar_pendientes", "subir_borradores", "publicar_youtube",
         "publicar_facebook", "publicar_tiktok_directo", "cerrar_distribucion"]
resto = sorted(set(tareas) - set(orden))
if resto:
    print(f"\nAVISO: tareas vivas no previstas, se conservan al final: {resto}")
orden = [k for k in orden if k in tareas] + resto
settings["tasks"] = [tareas[k] for k in orden]

settings["description"] = (
    "Produccion diaria de TikTok: guiones virales (opcionalmente con Apify), "
    "video animado con subtitulos, borrador en el inbox y publicacion en YouTube "
    "Shorts y Facebook Reels. Los videos se renderizan EN PARALELO: "
    "`seleccionar_guiones` decide el lote una sola vez (guiones, caras, hashtags) "
    "y `generar_video` es un for_each_task con concurrency=3 iteraciones "
    "simultaneas. Publicacion directa en TikTok deshabilitada: la app no esta "
    "auditada por TikTok."
)

SALIDA.write_text(
    json.dumps({"job_id": vivo["job_id"], "new_settings": settings},
               ensure_ascii=False, indent=1),
    encoding="utf-8",
)
print(f"\nescrito {SALIDA}")
print("tareas:", [t["task_key"] for t in settings["tasks"]])
