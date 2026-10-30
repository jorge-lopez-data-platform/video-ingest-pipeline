"""Compila todos los .py del pipeline.

Los notebooks exportados de Databricks (`# Databricks notebook source`) usan
`await` de nivel superior, así que se compilan con PyCF_ALLOW_TOP_LEVEL_AWAIT.
"""

import ast
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _archivos():
    salida = subprocess.check_output(
        ["git", "ls-files", "scripts/*.py", "ingest_lib/*.py", "notebooks/*.py"],
        cwd=ROOT,
        text=True,
    )
    return [ROOT / linea for linea in salida.split()]


@pytest.mark.parametrize("ruta", _archivos(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_compila(ruta):
    fuente = ruta.read_bytes().decode("utf-8-sig")
    compile(fuente, str(ruta), "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT, dont_inherit=True)
