"""Genera el payload de `jobs reset` con los canales de distribucion encendidos.

Lee `jobs/multicanal.job.json` (la definicion ya aplicada), pone `disabled: false`
en `publicar_youtube` y `publicar_facebook`, y escribe `jobs/multicanal.reset.json`
con el `job_id` dentro, que es la forma que acepta la CLI.

No aplica nada por si solo: imprime el comando que hay que ejecutar.

Uso:  python -I jobs/activar_canales.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

JOB_ID = 813880207704409
BASE = Path(__file__).resolve().parent
CANALES = ("publicar_youtube", "publicar_facebook")


def main() -> int:
    origen = BASE / "multicanal.job.json"
    if not origen.is_file():
        print(f"Falta {origen}. Ejecuta antes build_multicanal_job.py.")
        return 1

    settings = json.loads(origen.read_text(encoding="utf-8"))["settings"]

    vistos = set()
    for tarea in settings["tasks"]:
        if tarea["task_key"] in CANALES:
            tarea["disabled"] = False
            vistos.add(tarea["task_key"])

    faltan = set(CANALES) - vistos
    if faltan:
        print(f"No estan en la definicion: {sorted(faltan)}")
        return 1

    destino = BASE / "multicanal.reset.json"
    destino.write_text(
        json.dumps({"job_id": JOB_ID, "new_settings": settings},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Escrito {destino}")
    for tarea in settings["tasks"]:
        print(f"  {tarea['task_key']:22} disabled={tarea.get('disabled')}")
    print(f"  schedule: {settings['schedule']['pause_status']}")
    print()
    print("Aplicar con:")
    print(f"  databricks jobs reset --json @{destino} --profile DEFAULT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
