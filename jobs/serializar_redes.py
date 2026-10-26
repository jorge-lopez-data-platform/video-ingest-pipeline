"""Serializa las publicaciones en redes: eran ráfagas en paralelo.

`publicar_facebook` y `publicar_youtube` tienen `for_each_task` con
`concurrency` sin definir, y en Databricks eso significa **todas las
iteraciones a la vez**. Con 10-20 videos por corrida son 10-20 peticiones
simultáneas contra la misma página/API:

- Facebook: 13 fallos `start_400` entre el 2026-10-24T23:35 y el
  2026-10-25T05:49, y en cambio funcionó a las 06:47 y 07:06 con menos
  videos en cola. Es el patrón de límite de tasa, no de configuración rota.
- YouTube: `uploadLimitExceeded` en 5 casos (cuota diaria de la API, que
  se agota más rápido si se piden todas a la vez).

Es el mismo defecto que ya se corrigió en `subir_borradores` y que produjo
el `spam_risk_too_many_pending_share` de TikTok.

Este script NO aplica nada: escribe `jobs/concurrencia_redes.reset.json`
partiendo del job vivo (`jobs/job_live_20261025.json`).

Uso:
    python -I jobs/serializar_redes.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

JOB_ID = 813880207704409
BASE = Path(__file__).resolve().parent
ENTRADA = BASE / "job_live_20261025.json"
SALIDA = BASE / "concurrencia_redes.reset.json"

# Una a la vez: el cuello de botella es el límite de tasa de la plataforma, no
# el compute, así que paralelizar solo consigue que fallen más a la vez.
TAREAS = ("publicar_facebook", "publicar_youtube")


def main() -> int:
    if not ENTRADA.is_file():
        print(f"Falta {ENTRADA}. Genera con: "
              f"databricks jobs get {JOB_ID} -o json > jobs/job_live_20261025.json")
        return 1

    settings = json.loads(ENTRADA.read_text(encoding="utf-8-sig"))["settings"]
    for clave in ("job_id", "created_time", "creator_user_name", "run_as_user_name"):
        settings.pop(clave, None)

    tocadas = 0
    for task in settings["tasks"]:
        if task["task_key"] not in TAREAS:
            continue
        fe = task.get("for_each_task")
        if not fe:
            print(f"AVISO: {task['task_key']} no es for_each; se omite")
            continue
        antes = fe.get("concurrency")
        fe["concurrency"] = 1
        # Sin reintentos: un 400 de tasa no se arregla repitiendo de inmediato,
        # y el reintento automático es justo lo que convierte una ráfaga en un
        # bloqueo. La corrida siguiente recoge lo que quedó pendiente.
        it = fe["task"]
        it["max_retries"] = 0
        it["retry_on_timeout"] = False
        print(f"{task['task_key']}: concurrency {antes} -> 1, max_retries 0")
        tocadas += 1

    if not tocadas:
        print("ERROR: no se encontró ninguna tarea de red")
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
