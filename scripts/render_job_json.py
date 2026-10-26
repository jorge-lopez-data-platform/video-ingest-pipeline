#!/usr/bin/env python3
"""Rellena los placeholders de los payloads de job antes de aplicarlos.

`jobs/*.job.json` se versiona con placeholders (`USUARIO`, `tu_cuenta`) para no
publicar identificadores personales. Databricks no interpola variables en
`jobs reset`, asi que hay que renderizarlos a un archivo aparte.

Uso:
    python scripts/render_job_json.py \\
        --workspace-user jorge.lopez@example.com \\
        --own-handles alguien_que_te_escucha \\
        --out-dir build/jobs

    databricks jobs reset --json @build/jobs/ingest_daily.job.json --profile DEFAULT

El directorio de salida queda fuera de Git (ver .gitignore).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLACEHOLDER_USER = "USUARIO"
PLACEHOLDER_OWN_HANDLE = "tu_cuenta"
JOBS_DIR = Path(__file__).resolve().parent.parent / "jobs"


def render(payload: object, workspace_user: str, own_handles: str) -> object:
    """Sustituye los placeholders recursivamente en el payload ya parseado."""
    if isinstance(payload, dict):
        return {k: render(v, workspace_user, own_handles) for k, v in payload.items()}
    if isinstance(payload, list):
        return [render(v, workspace_user, own_handles) for v in payload]
    if isinstance(payload, str):
        return payload.replace(PLACEHOLDER_USER, workspace_user).replace(
            PLACEHOLDER_OWN_HANDLE, own_handles
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workspace-user",
        required=True,
        help="Usuario del workspace, tal como aparece en /Users/<usuario>",
    )
    parser.add_argument(
        "--own-handles",
        default=PLACEHOLDER_OWN_HANDLE,
        help=f"Cuentas propias separadas por coma (por defecto: {PLACEHOLDER_OWN_HANDLE})",
    )
    parser.add_argument(
        "--out-dir",
        default="build/jobs",
        help="Directorio de salida (por defecto: build/jobs)",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = sorted(JOBS_DIR.glob("*.job.json"))
    if not sources:
        print(f"No hay payloads en {JOBS_DIR}", file=sys.stderr)
        return 1

    for src in sources:
        payload = json.loads(src.read_text(encoding="utf-8-sig"))
        rendered = render(payload, args.workspace_user, args.own_handles)
        # Sin BOM y con UTF-8: un BOM rompe `jobs reset` con un error de JSON
        # que no menciona la causa (ver docs/SETUP.md).
        dst = out_dir / src.name
        dst.write_text(
            json.dumps(rendered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"{src.name} -> {dst}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
