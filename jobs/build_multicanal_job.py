"""Construye la definición del job multicanal a partir del job vivo.

No aplica nada: escribe `jobs/multicanal.job.json` para revisarlo y aplicarlo
después con `databricks jobs reset`.

Añade al DAG actual tres tareas:

  - publicar_youtube    for_each sobre los videos generados (desactivada hasta tener OAuth)
  - publicar_facebook   for_each sobre los videos generados (desactivada hasta tener el token)
  - cerrar_distribucion resumen único por Slack, con run_if ALL_DONE

Y cambia la cadencia de 3 diarios a 6 diarios de 1 video (cron 0,4,8,12,16,20 UTC),
que es el techo que permite la cuota de YouTube (10 000 unidades/día, 1 600 por subida).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WORKSPACE = "/Users/lopezechagarayjorge@gmail.com/tiktok_ingest"
SALIDA = Path(__file__).resolve().parent / "multicanal.job.json"

# Solo estas claves del job vivo se descartan; el resto las conserva la API.
DESCARTAR = {"job_id", "created_time", "creator_user_name", "run_as_user_name"}

CRON = "0 0 0,4,8,12,16,20 * * ?"


def tarea_for_each(key: str, notebook: str, extra: dict, disabled: bool = True) -> dict:
    """Tarea que itera sobre los videos de `generar_video`."""
    parametros = {"video_name": "{{input.video_name}}", **extra}
    return {
        "task_key": key,
        "depends_on": [{"task_key": "generar_video"}],
        "run_if": "ALL_DONE",          # un canal caído no bloquea a los otros
        # Sin environment_key ni timeout_seconds en la tarea exterior: la API
        # rechaza ambos en un for_each ("A task environment can only be provided
        # ... using serverless compute", "For each task cannot be timed out").
        # El entorno y el limite van en la tarea interior.
        "disabled": disabled,
        "for_each_task": {
            "inputs": "{{tasks.generar_video.values.videos}}",
            "task": {
                "task_key": f"{key}_item",
                "environment_key": "publish",
                "timeout_seconds": 1500,
                # Sin max_retries: la tarea ya es idempotente y los errores de
                # cuota o los deterministas no mejoran reintentando.
                "notebook_task": {
                    "notebook_path": f"{WORKSPACE}/{notebook}",
                    "source": "WORKSPACE",
                    "base_parameters": parametros,
                },
            },
        },
    }


def main() -> int:
    origen = Path(os.environ.get("JOB_LIVE_JSON", ""))
    if not origen.is_file():
        print("Falta JOB_LIVE_JSON apuntando al json de `databricks jobs get`.")
        return 1

    settings = json.loads(origen.read_text(encoding="utf-8-sig"))["settings"]
    for clave in DESCARTAR:
        settings.pop(clave, None)

    # --- cadencia: 6 al día de 1 video ---
    settings["schedule"] = {
        "pause_status": "PAUSED",   # se despausa al terminar la validación
        "quartz_cron_expression": CRON,
        "timezone_id": "UTC",
    }
    for prm in settings["parameters"]:
        if prm["name"] == "cantidad":
            prm["default"] = "1"

    # Ya existen en el job vivo: no duplicarlas al reejecutar este script.
    tareas = [t for t in settings["tasks"]
              if t["task_key"] not in
              ("publicar_youtube", "publicar_facebook", "cerrar_distribucion")]

    tareas.append(tarea_for_each("publicar_youtube", "publicar_youtube",
                                 {"privacy": "private"}))
    tareas.append(tarea_for_each("publicar_facebook", "publicar_facebook",
                                 {"publicar": "true"}))
    tareas.append({
        "task_key": "cerrar_distribucion",
        "depends_on": [{"task_key": "subir_borradores"},
                       {"task_key": "publicar_youtube"},
                       {"task_key": "publicar_facebook"}],
        "run_if": "ALL_DONE",
        "environment_key": "publish",
        "timeout_seconds": 900,
        "notebook_task": {
            "notebook_path": f"{WORKSPACE}/cerrar_distribucion",
            "source": "WORKSPACE",
        },
    })

    # TikTok deja de bloquear al resto: si YouTube cae, el borrador igual se sube.
    for t in tareas:
        if t["task_key"] == "subir_borradores":
            t["run_if"] = "ALL_DONE"
        # La API rechaza timeout_seconds en cualquier tarea for_each, aunque sea 0.
        # El job vivo trae un 0 en subir_borradores; hay que quitarlo.
        if t.get("for_each_task"):
            t.pop("timeout_seconds", None)

    settings["tasks"] = tareas
    SALIDA.write_text(json.dumps({"settings": settings}, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    print(f"Escrito {SALIDA}")
    print("Tareas:", [t["task_key"] for t in tareas])
    print("Cron:", CRON, "| pausado:", settings["schedule"]["pause_status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
