"""Arregla la sustitución de `{{input}}` en el for_each de render.

`generar_video` nunca ha funcionado con la arquitectura de lotes: en los tres
runs del 2026-10-25 todas las iteraciones murieron con

    ValueError: El ID '' no está en el manifiesto ... IDs: [...]

`{{input}}` no se sustituye sobre una lista de cadenas publicada como task
value, aunque `ids` llegue con contenido. El arreglo no depende de esa
sustitución:

- `inputs` pasa a ser `{{tasks.seleccionar_guiones.values.indices}}`, una lista
  de posiciones.
- La iteración recibe `indice` con `{{index}}`, que Databricks sí resuelve.
- `render_video_lote` resuelve el ID leyendo el manifiesto por esa posición.

Parte del job vivo para no deshacer otros cambios. No aplica nada: escribe
`jobs/indice_render.reset.json`.

Uso:  python -I jobs/arreglar_indice_render.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

JOB_ID = 813880207704409
BASE = Path(__file__).resolve().parent
ENTRADA = BASE / "job_vivo.json"
SALIDA = BASE / "indice_render.reset.json"


def main() -> int:
    if not ENTRADA.is_file():
        print(f"Falta {ENTRADA}. Genera con: databricks jobs get {JOB_ID} -o json")
        return 1

    settings = json.loads(ENTRADA.read_text(encoding="utf-8-sig"))["settings"]
    for clave in ("job_id", "created_time", "creator_user_name", "run_as_user_name"):
        settings.pop(clave, None)

    for task in settings["tasks"]:
        if task["task_key"] != "generar_video":
            continue
        ft = task["for_each_task"]
        antes = ft.get("inputs")
        ft["inputs"] = "{{tasks.seleccionar_guiones.values.indices}}"
        print(f"generar_video.inputs: {antes} -> {ft['inputs']}")
        params = ft["task"]["notebook_task"]["base_parameters"]
        params.pop("video_fuente_id", None)
        params["indice"] = "{{index}}"
        ft["task"]["notebook_task"]["base_parameters"] = params
        print(f"render_video params: {sorted(params)}")

    SALIDA.write_text(
        json.dumps({"job_id": JOB_ID, "new_settings": settings},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nEscrito {SALIDA}")
    print("\nAplicar con:")
    print(f"  databricks jobs reset --json @{SALIDA} --profile DEFAULT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
