"""Aplica el ritmo de subida que evita que TikTok lo lea como spam.

Parte del job **vivo** (`jobs/job_vivo.json`) para no deshacer los cambios que
hayan aplicado otros scripts o sesiones. Aplica tres cambios, todos consecuencia
de la medición del 2026-10-25:

1. **`concurrency: 1` en `subir_borradores`.** Estaba sin definir, y sin ese
   campo Databricks lanza *todas* las iteraciones en paralelo. Con
   `max_pendientes=1` da igual, pero en cuanto se suba ese tope se disparan N
   `init` simultáneos: exactamente el patrón que TikTok marcó como abuso
   (14 `init` en 4 minutos el 2026-10-24).
2. **`retry_on_timeout: false` y `max_retries: 0`** en la iteración, explícitos.
   El run del 2026-10-25 repitió una iteración tres veces con `max_retries: 0`
   en la definición; dejarlo explícito no lo garantiza, pero el subidor ya se
   protege solo con la reserva `INICIANDO`.
3. **`max_pendientes` a `20`**, que es el objetivo del dueño: hasta 20 videos
   al día. El cortacircuitos de cupo y la propia API siguen siendo el freno.

No aplica nada: escribe `jobs/ritmo_tiktok.reset.json` e imprime el comando.

Uso:  python -I jobs/aplicar_ritmo_tiktok.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

JOB_ID = 813880207704409
BASE = Path(__file__).resolve().parent
ENTRADA = BASE / "job_vivo.json"
SALIDA = BASE / "ritmo_tiktok.reset.json"


def main() -> int:
    if not ENTRADA.is_file():
        print(f"Falta {ENTRADA}. Genera con: databricks jobs get {JOB_ID} -o json")
        return 1

    settings = json.loads(ENTRADA.read_text(encoding="utf-8-sig"))["settings"]
    for clave in ("job_id", "created_time", "creator_user_name", "run_as_user_name"):
        settings.pop(clave, None)

    # --- 3: hasta 20 videos diarios -----------------------------------------
    cambios = {"max_pendientes": "20"}
    for prm in settings.get("parameters", []):
        if prm["name"] in cambios:
            nuevo = cambios.pop(prm["name"])
            print(f"{prm['name']}: {prm.get('default')!r} -> {nuevo!r}")
            prm["default"] = nuevo
    for faltante in cambios:
        print(f"AVISO: el job no tiene el parámetro {faltante}.")

    # --- 1 y 2: ritmo de la cola de subida ----------------------------------
    for task in settings["tasks"]:
        if task["task_key"] != "subir_borradores":
            continue
        ft = task["for_each_task"]
        antes = ft.get("concurrency")
        ft["concurrency"] = 1
        print(f"subir_borradores.concurrency: {antes!r} -> 1 "
              f"(antes se ejecutaban todas las iteraciones a la vez)")
        interior = ft["task"]
        interior["max_retries"] = 0
        interior["retry_on_timeout"] = False
        print("subir_borrador: max_retries=0, retry_on_timeout=false")

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
