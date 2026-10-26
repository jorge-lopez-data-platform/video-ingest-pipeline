"""Saca la privacidad de YouTube del código y la vuelve parámetro del job.

El job pasa `privacy: "private"` literal en el `base_parameters` de
`publicar_youtube`, así que todos los videos se suben privados aunque el
insert devuelva éxito: por eso "dice que se subió" y a la vez "dice video
privado". No es una restricción de Google ni del proyecto de GCP; es el valor
que el job manda en `status.privacyStatus`.

Este script NO aplica nada: parte del job vivo (`jobs/job_live_20261025.json`),
añade el job parameter `youtube_privacy` y hace que la tarea lo lea de ahí.
Así se cambia la privacidad con un `run-now` sin volver a tocar el job.

Uso:
    python -I jobs/arreglar_privacidad_youtube.py [--privacidad unlisted|public|private]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

JOB_ID = 813880207704409
BASE = Path(__file__).resolve().parent
ENTRADA = BASE / "job_live_20261025.json"
SALIDA = BASE / "privacidad_youtube.reset.json"
VALIDAS = ("private", "unlisted", "public")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--privacidad", default="public", choices=VALIDAS)
    args = ap.parse_args()

    if not ENTRADA.is_file():
        print(f"Falta {ENTRADA}. Genera con: "
              f"databricks jobs get {JOB_ID} -o json > jobs/job_live_20261025.json")
        return 1

    settings = json.loads(ENTRADA.read_text(encoding="utf-8-sig"))["settings"]
    for clave in ("job_id", "created_time", "creator_user_name", "run_as_user_name"):
        settings.pop(clave, None)

    # El job parameter es lo que permite cambiarlo por corrida. `default` queda
    # en el valor elegido: quien quiera otra cosa lo pasa en el `run-now`.
    parametros = settings.setdefault("parameters", [])
    existente = next((p for p in parametros if p["name"] == "youtube_privacy"), None)
    if existente is None:
        parametros.append({"name": "youtube_privacy", "default": args.privacidad})
        print(f"job parameter youtube_privacy añadido (default {args.privacidad})")
    else:
        print(f"job parameter youtube_privacy ya existía: {existente['default']} "
              f"-> {args.privacidad}")
        existente["default"] = args.privacidad

    tocada = False
    for task in settings["tasks"]:
        if task["task_key"] != "publicar_youtube":
            continue
        base = task["for_each_task"]["task"]["notebook_task"]["base_parameters"]
        antes = base.get("privacy")
        base["privacy"] = "{{job.parameters.youtube_privacy}}"
        print(f"publicar_youtube.privacy: {antes!r} -> {base['privacy']}")
        tocada = True

    if not tocada:
        print("ERROR: no se encontró la tarea publicar_youtube")
        return 1

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
