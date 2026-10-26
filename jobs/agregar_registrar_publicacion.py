"""Genera el payload de `jobs update` que agrega la tarea `registrar_publicacion`.

Lee la definicion VIVA del job (`databricks jobs get`, solo lectura) o un
archivo con `--desde`, inserta la tarea y hace que `cerrar_distribucion`
dependa tambien de ella. Escribe `jobs/registrar_publicacion.job.json`.

NO aplica nada: imprime el comando. Seguro de reejecutar: si la tarea ya
existe no se duplica, solo se normalizan sus dependencias.

Uso:  python -I jobs/agregar_registrar_publicacion.py [--desde job.json]
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

JOB_ID = 638404369584915
BASE = Path(__file__).resolve().parent
TAREA = "registrar_publicacion"
CIERRE = "cerrar_distribucion"
PUBLICACIONES = ("subir_borradores", "publicar_youtube", "publicar_facebook")


def agregar_tarea(tasks: list[dict]) -> list[dict]:
    """Devuelve una copia de `tasks` con la tarea nueva y el cierre enlazado."""
    tasks = copy.deepcopy(tasks)
    por_clave = {t["task_key"]: t for t in tasks}
    faltan = [k for k in (*PUBLICACIONES, CIERRE) if k not in por_clave]
    if faltan:
        raise ValueError(f"Faltan tareas en el job: {faltan}")

    deps = [{"task_key": k} for k in PUBLICACIONES]
    cierre = por_clave[CIERRE]

    if TAREA in por_clave:
        por_clave[TAREA]["depends_on"] = deps
        por_clave[TAREA]["run_if"] = "ALL_DONE"
    else:
        ruta = cierre["notebook_task"]["notebook_path"].rsplit("/", 1)[0]
        nueva = {
            "task_key": TAREA,
            "depends_on": deps,
            "run_if": "ALL_DONE",
            "disabled": False,
            "email_notifications": {},
            "notebook_task": {"notebook_path": f"{ruta}/{TAREA}", "source": "WORKSPACE"},
            "timeout_seconds": 900,
        }
        if cierre.get("environment_key"):
            nueva["environment_key"] = cierre["environment_key"]
        # Justo antes del cierre, para que la lista se lea en orden de ejecucion.
        tasks.insert(tasks.index(cierre), nueva)

    # El cierre espera a las tres publicaciones Y al registro.
    claves = [d["task_key"] for d in cierre.get("depends_on", [])]
    if TAREA not in claves:
        cierre.setdefault("depends_on", []).append({"task_key": TAREA})
    return tasks


def cargar(desde: str | None) -> dict:
    if desde:
        return json.loads(Path(desde).read_text(encoding="utf-8"))
    salida = subprocess.run(
        ["databricks", "jobs", "get", str(JOB_ID), "--profile", "DEFAULT", "-o", "json"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return json.loads(salida)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", help="job.json ya descargado en vez de `jobs get`")
    args = ap.parse_args()
    try:
        job = cargar(args.desde)
        tasks = agregar_tarea(job["settings"]["tasks"])
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        print(f"No se pudo generar el payload: {exc}")
        return 1

    destino = BASE / "registrar_publicacion.job.json"
    destino.write_text(
        json.dumps({"job_id": JOB_ID, "new_settings": {"tasks": tasks}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Escrito {destino}")
    for t in tasks:
        print(f"  {t['task_key']:22} <- {[d['task_key'] for d in t.get('depends_on', [])]}")
    print()
    print("Sube antes el notebook al workspace; luego aplicar con:")
    print(f"  databricks jobs update --json @{destino} --profile DEFAULT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
