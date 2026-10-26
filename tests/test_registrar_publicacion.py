"""Tests de la logica pura de `registrar_publicacion.py` y del script de job.

El notebook usa `dbutils`/`spark` y no se puede importar entero. Sus funciones
puras estan arriba del todo, hasta el marcador `FIN FUNCIONES PURAS`; aqui se
ejecuta solo ese tramo (el notebook sigue siendo desplegable de forma autonoma).
"""

import importlib.util
import re
from datetime import datetime
from pathlib import Path

import pytest

from slackbot.nombres import canonico as canonico_repo

ROOT = Path(__file__).resolve().parent.parent
MARCADOR = "# --- FIN FUNCIONES PURAS"


def _cargar_notebook():
    src = (ROOT / "registrar_publicacion.py").read_text(encoding="utf-8")
    assert MARCADOR in src
    ns = {}
    exec(compile(src.split(MARCADOR)[0], "registrar_publicacion.py", "exec"), ns)
    return ns


NB = _cargar_notebook()

NOMBRES = [
    "x.mp4", "x_v2.mp4", "v2_x.mp4", "x_v2", "x", "", None, "a_v2_v2.mp4",
    "a.b_v2", "a.b_v2.mp4", "_v2.mp4", "x_v2b.mp4", "x_v2.tar.gz", "x_V2.mp4",
]


@pytest.mark.parametrize("nombre", NOMBRES)
def test_canonico_igual_que_slackbot(nombre):
    assert NB["canonico"](nombre) == canonico_repo(nombre)


def test_canonico_ejemplo():
    assert NB["canonico"]("x_v2.mp4") == "x.mp4"


def _spark_regex(nombre):
    """Aplica los dos regexp_replace de Spark con `re` (Java $1 -> \\1)."""
    s = nombre or ""
    for patron, repl in (NB["CANON_REGEX_A"], NB["CANON_REGEX_B"]):
        s = re.sub(patron, re.sub(r"\$(\d)", r"\\\1", repl), s)
    return s


@pytest.mark.parametrize("nombre", NOMBRES)
def test_regex_spark_equivale_a_canonico(nombre):
    assert _spark_regex(nombre) == canonico_repo(nombre)


@pytest.mark.parametrize("origen,esperado", [
    ("PUBLICADO", "PUBLICADO"),
    ("PUBLISH_COMPLETE", "PUBLICADO"),
    ("PUBLICADO_PRIVADO", "PUBLICADO_PRIVADO"),
    ("SEND_TO_USER_INBOX", "EN_BORRADOR"),
    ("SKIPPED_CUOTA", "SALTADO"),
    ("SKIPPED_INBOX_FULL", "SALTADO"),
    ("FALLIDO", "FALLIDO"),
])
def test_estado_unificado_vocabulario(origen, esperado):
    assert NB["estado_unificado"](origen) == esperado


def test_estado_desconocido_con_motivo_es_fallido():
    assert NB["estado_unificado"]("start_400", "rate limit") == "FALLIDO"


def test_estado_desconocido_sin_motivo_no_inventa_exito():
    assert NB["estado_unificado"]("EN_CURSO") == "DESCONOCIDO"
    assert NB["estado_unificado"](None) == "DESCONOCIDO"


# --- clave de intento (Ruling 19) -------------------------------------------

def test_clave_intento_sin_marca_es_cadena_vacia():
    # Constante para distribucion_video: el MERGE actualiza, no inserta.
    assert NB["clave_intento"](None) == ""
    assert NB["clave_intento"]("") == ""


def test_clave_intento_no_distingue_marcas_iguales():
    # Dos intentos con la MISMA marca son el mismo intento, no dos filas.
    t = datetime(2026, 10, 24, 22, 2, 40, 817000)
    assert NB["clave_intento"](t) == NB["clave_intento"](t)


def test_formato_spark_es_la_traduccion_del_de_python():
    """El formato de Spark esta escrito a mano; este test lo ata al de Python.

    Si alguien cambia FORMATO_INTENTO o FORMATO_INTENTO_SPARK sin el otro, el
    pipeline escribiria claves que no corresponden a lo que documenta el spec.
    """
    assert NB["formato_spark"](NB["FORMATO_INTENTO"]) == NB["FORMATO_INTENTO_SPARK"]


def test_la_clave_del_merge_y_la_deduplicacion_usan_la_misma_constante():
    """La clave no puede estar escrita dos veces a mano: divergirian."""
    src = (ROOT / "registrar_publicacion.py").read_text(encoding="utf-8")
    on = src.split("MERGE INTO")[1].split("WHEN MATCHED")[0]
    assert "CLAVE" in on
    assert "partitionBy(*CLAVE)" in src
    # Ninguna columna de la clave puede estar escrita literal en el ON.
    for col in NB["CLAVE"]:
        assert f"t.{col} =" not in on


def test_la_clave_es_exactamente_lo_que_dice_el_spec():
    # Si alguien quita clave_intento "porque run_id basta", esto falla.
    assert NB["CLAVE"] == ("video_name", "canal", "run_id", "clave_intento")


def test_la_migracion_corre_fuera_del_if_de_alter():
    """Si el UPDATE/DELETE quedan dentro del `if`, un fallo a medias deja filas
    en NULL para siempre: la siguiente corrida ya no entra en el `if`."""
    src = (ROOT / "registrar_publicacion.py").read_text(encoding="utf-8")
    migracion = src.split("Migracion, idempotente")[1].split("createOrReplaceTempView")[0]
    # El UPDATE del NULL y el COUNT deben correr SIEMPRE (4 espacios).
    siempre = [l for l in migracion.splitlines()
               if "UPDATE {LOG}" in l or "SELECT COUNT(*) AS n FROM {LOG}" in l]
    assert len(siempre) == 2, siempre
    for linea in siempre:
        assert linea.startswith("    ") and not linea.startswith("        "), \
            f"dentro de un if: {linea!r}"
    # El DELETE puede ir bajo `if borradas:`, que es idempotente por si mismo,
    # pero no bajo el `if` del ALTER.
    assert "if borradas:" in migracion
    assert migracion.find("if borradas:") < migracion.find("DELETE FROM {LOG}")
    assert 'if "clave_intento" not in actuales' not in migracion


def test_la_migracion_borra_las_filas_viejas_de_tiktok():
    """El esquema viejo perdia los intentos de TikTok; esas filas se reconstruyen."""
    src = (ROOT / "registrar_publicacion.py").read_text(encoding="utf-8")
    assert "DELETE FROM {LOG} WHERE canal = 'tiktok' AND clave_intento = ''" in src


def test_la_clave_no_depende_de_la_zona_horaria():
    """date_format depende de spark.sql.session.timeZone; unix_micros no."""
    src = (ROOT / "registrar_publicacion.py").read_text(encoding="utf-8")
    # La expresion completa que produce la clave de TikTok.
    expr = src.split("F.unix_micros")[0] if "F.unix_micros" in src else ""
    assert expr, "clave_intento ya no usa unix_micros"
    # Y no debe quedar ningun date_format alimentando la clave.
    cerca = src[src.find('alias("clave_intento")') - 300:
                src.find('alias("clave_intento")')]
    assert "date_format" not in cerca


# --- jobs/agregar_registrar_publicacion.py ----------------------------------

def _script():
    spec = importlib.util.spec_from_file_location(
        "agregar", ROOT / "jobs" / "agregar_registrar_publicacion.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tareas():
    def t(k, dep=None):
        d = {"task_key": k}
        if dep:
            d["depends_on"] = [{"task_key": x} for x in dep]
        return d
    cierre = t("cerrar_distribucion", ["subir_borradores", "publicar_youtube", "publicar_facebook"])
    cierre["environment_key"] = "publish"
    cierre["notebook_task"] = {"notebook_path": "/Users/u/tiktok_ingest/cerrar_distribucion"}
    return [t("subir_borradores"), t("publicar_youtube"), t("publicar_facebook"), cierre]


def test_job_agrega_tarea_y_enlaza_cierre():
    out = _script().agregar_tarea(_tareas())
    claves = [x["task_key"] for x in out]
    assert claves.count("registrar_publicacion") == 1
    nueva = out[claves.index("registrar_publicacion")]
    assert nueva["run_if"] == "ALL_DONE"
    assert [d["task_key"] for d in nueva["depends_on"]] == [
        "subir_borradores", "publicar_youtube", "publicar_facebook"]
    assert nueva["notebook_task"]["notebook_path"] == "/Users/u/tiktok_ingest/registrar_publicacion"
    cierre = out[claves.index("cerrar_distribucion")]
    assert {"task_key": "registrar_publicacion"} in cierre["depends_on"]


def test_job_es_idempotente():
    s = _script()
    una = s.agregar_tarea(_tareas())
    dos = s.agregar_tarea(una)
    assert una == dos


def test_job_falla_si_faltan_tareas():
    with pytest.raises(ValueError):
        _script().agregar_tarea([{"task_key": "otra"}])
