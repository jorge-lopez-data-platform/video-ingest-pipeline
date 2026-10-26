"""Recablea el DAG para que los pendientes se re-suban de verdad.

Antes:

    generar_video -> subir_borradores -> listar_pendientes

`listar_pendientes` solo escribia una lista que nadie leia, asi que un video que
se saltaba por el cupo del inbox no se reintentaba nunca.

Despues:

    generar_video -> listar_pendientes -> subir_borradores -> listar_pendientes_fin

`listar_pendientes` une los videos nuevos con los pendientes (tope 3) y
`subir_borradores` itera sobre esa cola. El cierre queda como tarea aparte para
que el resumen final siga existiendo.

Lee `jobs/job_vivo.json` (salida de `databricks jobs get`) y escribe
`jobs/pendientes.reset.json`. No aplica nada.

Uso:  python -I jobs/reordenar_pendientes.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

JOB_ID = 813880207704409
BASE = Path(__file__).resolve().parent
ENTRADA = BASE / "job_vivo.json"
SALIDA = BASE / "pendientes.reset.json"


def hallar(tareas, clave):
    for t in tareas:
        if t["task_key"] == clave:
            return t
    raise KeyError(clave)


def main() -> int:
    if not ENTRADA.is_file():
        print(f"Falta {ENTRADA}. Genera con: databricks jobs get {JOB_ID} --output json")
        return 1

    settings = json.loads(ENTRADA.read_text(encoding="utf-8-sig"))["settings"]
    for clave in ("job_id", "created_time", "creator_user_name", "run_as_user_name"):
        settings.pop(clave, None)

    tareas = settings["tasks"]

    # Tope de pendientes re-subidos por corrida. 20 drena rapido cuando el inbox
    # se libera, aunque con el cupo lleno solo consigue 20 rechazos en vez de 3.
    for prm in settings.get("parameters", []):
        if prm["name"] == "max_pendientes":
            prm["default"] = "20"

    listar = hallar(tareas, "listar_pendientes")
    subir = hallar(tareas, "subir_borradores")
    cerrar = hallar(tareas, "cerrar_distribucion")
    generar = hallar(tareas, "generar_video")

    # 1. listar_pendientes pasa a depender de generar_video: necesita sus
    #    taskValues para no re-subir lo recien generado.
    listar["depends_on"] = [{"task_key": "generar_video"}]
    listar["run_if"] = "ALL_DONE"          # si generar falla, aun quedan pendientes
    listar["notebook_task"]["base_parameters"] = {
        "max_pendientes": "{{job.parameters.max_pendientes}}",
    }

    # 2. subir_borradores itera sobre la cola de listar_pendientes, no sobre
    #    generar_video. Asi los pendientes de corridas anteriores entran.
    subir["depends_on"] = [{"task_key": "listar_pendientes"}]
    subir["for_each_task"]["inputs"] = "{{tasks.listar_pendientes.values.videos}}"

    # 3. El resumen de Slack ya no puede depender de listar_pendientes (que ahora
    #    corre al principio), asi que se cuelga de subir_borradores. Conserva su
    #    task_key: es el cierre del run, no un listado.
    dependencias = []
    for t in tareas:
        if t["task_key"] in ("subir_borradores", "publicar_youtube", "publicar_facebook"):
            dependencias.append({"task_key": t["task_key"]})
    cerrar["depends_on"] = dependencias

    # generar_video ya no alimenta a subir_borradores: se cae la dependencia
    # implicita y queda solo como productor de taskValues.
    generar.setdefault("depends_on", [])

    destino = SALIDA
    destino.write_text(
        json.dumps({"job_id": JOB_ID, "new_settings": settings},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Escrito {destino}")
    print()
    for t in sorted(tareas, key=lambda x: x["task_key"]):
        deps = ", ".join(d["task_key"] for d in t.get("depends_on", [])) or "-"
        fe = t.get("for_each_task")
        nota = f"  for_each={fe['inputs']}" if fe else ""
        print(f"  {t['task_key']:24} deps=[{deps}]  run_if={t.get('run_if')}{nota}")

    print()
    print("Aplicar con:")
    print(f"  databricks jobs reset --json @{destino} --profile DEFAULT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
