"""Ajusta la cadencia del job al techo real de TikTok.

TikTok permite como mucho 5 subidas al inbox por ventana movil de 24 h
(`spam_risk_too_many_pending_share`, documentado en la referencia de
`/post/publish/inbox/video/init/`). El job estaba a 6/dia, asi que perdia una
de cada seis por construccion.

Este script baja a 4/dia (margen de uno para reintentos) y el tope de
pendientes a 2, para no gastar el cupo drenando la cola.

Lee `jobs/job_vivo.json` y escribe `jobs/cadencia.reset.json`. No aplica nada.

Uso:  python -I jobs/ajustar_cadencia_tiktok.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

JOB_ID = 813880207704409
CRON = "0 0 2,8,14,20 * * ?"   # 02, 08, 14, 20 UTC -> 4 al dia
MAX_PENDIENTES = "2"

BASE = Path(__file__).resolve().parent
# Se encadena sobre el payload reordenado, no sobre el job vivo: partir de
# job_vivo.json desharia el reordenado del DAG que ya genera
# reordenar_pendientes.py.
REORDENADO = BASE / "pendientes.reset.json"
ENTRADA = BASE / "job_vivo.json"
SALIDA = BASE / "cadencia.reset.json"


def main() -> int:
    # Preferencia: el payload ya reordenado, para no perder ese cambio.
    if REORDENADO.is_file():
        settings = json.loads(
            REORDENADO.read_text(encoding="utf-8"))["new_settings"]
        print(f"Base: {REORDENADO.name} (DAG reordenado incluido)")
        for clave in ("job_id", "created_time", "creator_user_name", "run_as_user_name"):
            settings.pop(clave, None)
    elif ENTRADA.is_file():
        settings = json.loads(ENTRADA.read_text(encoding="utf-8-sig"))["settings"]
        print(f"Base: {ENTRADA.name} (sin reordenar)")
        for clave in ("job_id", "created_time", "creator_user_name", "run_as_user_name"):
            settings.pop(clave, None)
    else:
        print(f"Falta {REORDENADO} y {ENTRADA}.")
        print(f"Genera el reordenado primero: python -I jobs/reordenar_pendientes.py")
        return 1

    anterior = settings["schedule"].get("quartz_cron_expression")
    settings["schedule"]["quartz_cron_expression"] = CRON

    vistos = False
    for prm in settings.get("parameters", []):
        if prm["name"] == "max_pendientes":
            print(f"max_pendientes: {prm.get('default')} -> {MAX_PENDIENTES}")
            prm["default"] = MAX_PENDIENTES
            vistos = True
    if not vistos:
        print("AVISO: el job no tiene el parametro max_pendientes.")

    SALIDA.write_text(
        json.dumps({"job_id": JOB_ID, "new_settings": settings},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Cron: {anterior} -> {CRON}  (4 al dia; el techo de TikTok es 5)")
    print(f"Escrito {SALIDA}")
    print()
    print("Aplicar con:")
    print(f"  databricks jobs reset --json @{SALIDA} --profile DEFAULT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
