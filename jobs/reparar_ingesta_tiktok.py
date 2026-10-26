"""Genera el payload que repara la ingesta de TikTok y añade Direct Post.

Parte del job **vivo** (`databricks jobs get`) para no deshacer cambios aplicados
por otros scripts. Aplica cuatro cambios, todos ellos consecuencia del
diagnóstico del 2026-10-25:

1. **`allow_resubmit` a `false`.** Estaba en `true`, lo que desactivaba la
   protección SHA-256 del subidor y permitía reenviar el mismo archivo
   indefinidamente. Un mismo video llegó a intentarse 3 veces.
2. **`max_pendientes` a `1`.** La cola re-encolaba en cada corrida los pendientes
   más antiguos —justo los que ya habían fallado— y se comía el cupo antes de
   llegar a los nuevos. Con 1 solo se drena la cola sin competir con lo nuevo.
3. **`max_retries` de `subir_borrador` a `0`.** Los errores de cupo son
   deterministas: reintentar 3 veces un `spam_risk_*` es exactamente la ráfaga
   que TikTok marca como abuso (14 `init` en 4 min el 2026-10-24).
4. **Nueva task `publicar_tiktok_directo`**, hermana de `publicar_youtube` y
   `publicar_facebook`, que usa `post/publish/video/init/`. Queda **desactivada**
   (`disabled: true`) hasta que TikTok apruebe la auditoría de Content Posting;
   hoy respondería `unaudited_client_can_only_post_to_private_accounts`.

No aplica nada: escribe `jobs/reparo_tiktok.reset.json` e imprime el comando.

Uso:  python -I jobs/reparar_ingesta_tiktok.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

JOB_ID = 813880207704409
BASE = Path(__file__).resolve().parent
ENTRADA = BASE / "job_vivo.json"
SALIDA = BASE / "reparo_tiktok.reset.json"

NOMBRE_TASK_DIRECTO = "publicar_tiktok_directo"
RUTA_NOTEBOOK_DIRECTO = (
    "/Users/lopezechagarayjorge@gmail.com/tiktok_ingest/publicar_tiktok_directo"
)


def main() -> int:
    if not ENTRADA.is_file():
        print(f"Falta {ENTRADA}. Genera con: databricks jobs get {JOB_ID} -o json")
        return 1

    settings = json.loads(ENTRADA.read_text(encoding="utf-8-sig"))["settings"]
    for clave in ("job_id", "created_time", "creator_user_name", "run_as_user_name"):
        settings.pop(clave, None)

    # --- 1 y 2: parámetros del job -------------------------------------------
    cambios = {"allow_resubmit": "false", "max_pendientes": "1"}
    for prm in settings.get("parameters", []):
        if prm["name"] in cambios:
            nuevo = cambios.pop(prm["name"])
            print(f"{prm['name']}: {prm.get('default')!r} -> {nuevo!r}")
            prm["default"] = nuevo
    for faltante in cambios:
        print(f"AVISO: el job no tiene el parámetro {faltante}.")

    # --- 3: reintentos de la tarea de subida ---------------------------------
    for task in settings["tasks"]:
        if task["task_key"] == "subir_borradores":
            interior = task["for_each_task"]["task"]
            print(f"max_retries subir_borrador: {interior.get('max_retries')} -> 0")
            interior["max_retries"] = 0
            interior.pop("min_retry_interval_millis", None)
        elif task["task_key"] == "listar_pendientes":
            # La cola debe ver los nuevos: sin esto, un pendiente recién saltado
            # por cupo se vuelve a encolar en la misma corrida.
            task["run_if"] = "ALL_DONE"

    # --- 4: task de Direct Post ---------------------------------------------
    existentes = {t["task_key"] for t in settings["tasks"]}
    if NOMBRE_TASK_DIRECTO in existentes:
        print(f"{NOMBRE_TASK_DIRECTO} ya existe; no se duplica.")
    else:
        settings["tasks"].append({
            "task_key": NOMBRE_TASK_DIRECTO,
            "depends_on": [{"task_key": "generar_video"}],
            "disabled": True,
            # Ojo: en una task `for_each`, `environment_key` y `timeout_seconds`
            # van SOLO en la tarea interior. La API los rechaza en la exterior.
            "for_each_task": {
                "inputs": "{{tasks.generar_video.values.videos}}",
                "task": {
                    "task_key": "publicar_tiktok_directo_item",
                    "environment_key": "publish",
                    "notebook_task": {
                        "notebook_path": RUTA_NOTEBOOK_DIRECTO,
                        "source": "WORKSPACE",
                        "base_parameters": {
                            "video_name": "{{input.video_name}}",
                            "privacy_level": "",
                            "publicar": "true",
                        },
                    },
                    "run_if": "ALL_SUCCESS",
                    "timeout_seconds": 1500,
                },
            },
            "run_if": "ALL_DONE",
        })
        print(f"Añadida task {NOMBRE_TASK_DIRECTO} (disabled=true hasta la auditoría).")

        # cerrar_distribucion debe esperarla para que el resumen la incluya.
        for task in settings["tasks"]:
            if task["task_key"] == "cerrar_distribucion":
                deps = task.setdefault("depends_on", [])
                if not any(d.get("task_key") == NOMBRE_TASK_DIRECTO for d in deps):
                    deps.append({"task_key": NOMBRE_TASK_DIRECTO})
                    print("cerrar_distribucion ahora depende también de Direct Post.")

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
