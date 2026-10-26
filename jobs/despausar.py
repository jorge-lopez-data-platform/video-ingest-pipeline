"""Despausa el job multicanal.

Genera `jobs/despausar.reset.json` a partir de la configuracion viva (que ya
tiene el DAG reordenado, el cron de 4/dia y max_pendientes=2).

No aplica nada: imprime el comando.

Antes de ejecutar esto, comprueba que el token de pagina de Facebook esta
guardado. Si no lo esta, el canal seguira fallando con start_400 en cada run:
el job no se rompe (run_if ALL_DONE), pero esos reels se pierden.

Uso:  python -I jobs/despausar.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

JOB_ID = 813880207704409
BASE = Path(__file__).resolve().parent
ENTRADA = BASE / "job_vivo.json"
SALIDA = BASE / "despausar.reset.json"


def main() -> int:
    if not ENTRADA.is_file():
        print(f"Falta {ENTRADA}. Genera con: databricks jobs get {JOB_ID} --output json")
        return 1

    settings = json.loads(ENTRADA.read_text(encoding="utf-8-sig"))["settings"]
    for clave in ("job_id", "created_time", "creator_user_name", "run_as_user_name"):
        settings.pop(clave, None)

    anterior = settings["schedule"].get("pause_status")
    settings["schedule"]["pause_status"] = "UNPAUSED"

    SALIDA.write_text(
        json.dumps({"job_id": JOB_ID, "new_settings": settings},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"pause_status: {anterior} -> UNPAUSED")
    print(f"cron: {settings['schedule']['quartz_cron_expression']}")
    print(f"Escrito {SALIDA}")
    print()
    print("Aplicar con:")
    print(f"  databricks jobs reset --json @{SALIDA} --profile DEFAULT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
