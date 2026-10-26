"""Selecciona un guion de `tiktok.gold.guiones_60s` y lo exporta a guion.json.

Solo acepta guiones con confianza 'alta': un dato dudoso en un video de una
cuenta nueva cuesta más que saltarse un guion de una cola que ya tiene otros.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from scripts import common

TABLA = "tiktok.gold.guiones_60s"
CONFIANZA_REQUERIDA = "alta"


def _normalizar(valor) -> str:
    return str(valor).strip().lower() if valor is not None else ""


def seleccionar_guion(filas: list[dict]) -> dict:
    """Devuelve la fila de confianza alta con más plays de la fuente."""
    validas = [f for f in filas if _normalizar(f.get("confianza")) == CONFIANZA_REQUERIDA]
    if not validas:
        raise LookupError(
            f"No hay guiones con confianza '{CONFIANZA_REQUERIDA}' en {TABLA}. "
            "Genera más guiones o revisa los datos a verificar de los existentes."
        )
    validas.sort(key=lambda f: int(f.get("fuente_plays") or 0), reverse=True)
    return validas[0]


def _slug(fila: dict) -> str:
    fuente = fila.get("video_fuente_id")
    if fuente:
        return str(fuente)
    texto = (fila.get("hook") or "guion").lower()
    palabras = re.findall(r"[a-záéíóúüñ0-9]+", texto)[:6]
    return "-".join(palabras) or "guion"


def construir_guion_json(fila: dict) -> dict:
    """Convierte una fila de Delta en el guion que consumen las demás etapas."""
    hashtags = [h.strip() for h in str(fila.get("hashtags") or "").split(",") if h.strip()]
    datos = [d.strip() for d in str(fila.get("datos_a_verificar") or "").split("|") if d.strip()]
    pantalla = [fila.get(f"texto_pantalla_{i}") for i in (1, 2, 3)]
    pantalla = [p for p in pantalla if p and str(p).strip()]

    return {
        "slug": _slug(fila),
        "hook": fila.get("hook") or "",
        "open_loop": fila.get("open_loop") or "",
        "bloque_1": fila.get("bloque_1") or "",
        "pattern_interrupt": fila.get("pattern_interrupt") or "",
        "bloque_2": fila.get("bloque_2") or "",
        "pre_cierre": fila.get("pre_cierre") or "",
        "cierre": fila.get("cierre") or "",
        "cta": fila.get("cta") or "",
        "hashtags": hashtags,
        "texto_en_pantalla": [str(p).strip() for p in pantalla],
        "datos_a_verificar": datos,
        "palabras_objetivo": int(fila.get("palabras_reales") or 0),
    }


def consultar_tabla(perfil: str = "DEFAULT") -> list[dict]:
    """Lee la tabla vía la CLI de Databricks y devuelve las filas como dicts."""
    sql = f"SELECT * FROM {TABLA}"
    resultado = subprocess.run(
        ["databricks", "experimental", "aitools", "tools", "query",
         "--profile", perfil, "--output", "json", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"La consulta falló: {resultado.stderr.strip()[:400]}")
    return json.loads(resultado.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="guion.json", help="Ruta de salida")
    parser.add_argument("--profile", default="DEFAULT", help="Perfil de la CLI")
    parser.add_argument("--no-confirm", action="store_true",
                        help="Escribe sin pedir confirmación")
    args = parser.parse_args(argv)
    common.configurar_salida()

    try:
        filas = consultar_tabla(args.profile)
        fila = seleccionar_guion(filas)
    except LookupError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    guion = construir_guion_json(fila)

    print(f"Guion: {guion['slug']}")
    print(f"Hook:  {guion['hook']}")
    print(f"Palabras: {guion['palabras_objetivo']}")
    if guion["datos_a_verificar"]:
        print("\nDatos a verificar antes de publicar:")
        for dato in guion["datos_a_verificar"]:
            print(f"  [ ] {dato}")
    else:
        print("\nSin datos concretos a verificar.")

    if not args.no_confirm:
        respuesta = input("\n¿Producir este video? [s/N] ").strip().lower()
        if respuesta not in ("s", "si", "sí", "y", "yes"):
            print("Cancelado.")
            return 1

    Path(args.out).write_text(json.dumps(guion, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ Escrito {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
