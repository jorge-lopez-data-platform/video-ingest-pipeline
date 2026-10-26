# Databricks notebook source
# MAGIC %md
# MAGIC # Sonda: por qué ffmpeg falla al cerrar el mp4
# MAGIC Reproduce el render con 5 s de audio en tres destinos distintos
# MAGIC (`/tmp`, volumen, mp4 fragmentado) y muestra solo las líneas de error
# MAGIC del log, que es donde está la causa que el mensaje truncado esconde.

# COMMAND ----------

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

LIB = "/Volumes/tiktok/default/videos/lib"
VIDEOS_DIR = "/Volumes/tiktok/default/videos"
FONDO = f"{VIDEOS_DIR}/assets/paraiso_ia.png"

sys.path.insert(0, LIB)
from scripts import common, jesucitos  # noqa: E402

resultado = {}

# COMMAND ----------

# MAGIC %md ## 1. Espacio libre en cada destino

# COMMAND ----------

for nombre, ruta in [("/tmp", "/tmp"), ("volumen", VIDEOS_DIR)]:
    try:
        u = shutil.disk_usage(ruta)
        resultado[f"espacio_{nombre}"] = {
            "total_gb": round(u.total / 1e9, 2),
            "libre_gb": round(u.free / 1e9, 2),
        }
    except Exception as exc:
        resultado[f"espacio_{nombre}"] = f"ERROR {type(exc).__name__}: {exc}"

# COMMAND ----------

# MAGIC %md ## 2. Audio corto (5 s) para que el fallo aparezca en un minuto

# COMMAND ----------

import edge_tts

texto = "Dios te está viendo. Esto es solo una prueba del render."
audio = bytearray()
async for trozo in edge_tts.Communicate(texto, "es-MX-JorgeNeural").stream():
    if trozo["type"] == "audio":
        audio.extend(trozo["data"])

tmp = Path(tempfile.mkdtemp(prefix="probe_render_", dir=VIDEOS_DIR))
audio_path = tmp / "voz.mp3"
audio_path.write_bytes(bytes(audio))
resultado["duracion_audio"] = common.probe(audio_path)["duracion"]

# COMMAND ----------

# MAGIC %md ## 3. Render completo copiando `animar`, con destinos intercambiables

# COMMAND ----------


def lineas_de_error(log_bytes):
    """Solo las líneas que explican el fallo, no el banner ni las estadísticas."""
    claves = ("rror", "space", "moov", "interleaved", "Invalid", "failed",
              "No such", "Permission", "denied", "truncat", "wrote")
    return [
        l.strip() for l in log_bytes.decode("utf-8", "replace").splitlines()
        if any(c in l for c in claves)
    ]


def render(destino, extra_args=()):
    info = common.probe(audio_path)
    duracion = info["duracion"]
    fondo = jesucitos.cargar_fondo(Path(FONDO) if os.path.isfile(FONDO) else None)
    aperturas = jesucitos.aperturas_boca(jesucitos.envolvente_audio(audio_path))
    n_frames = max(1, int(round(duracion * jesucitos.FPS)))
    aperturas += [0.0] * (n_frames - len(aperturas))
    parpadeo = jesucitos.parpadeos(n_frames)

    salida = Path(destino)
    salida.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as log:
        proceso = subprocess.Popen(
            [common.FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{jesucitos.ANCHO}x{jesucitos.ALTO}", "-r", str(jesucitos.FPS),
             "-i", "-", "-i", str(audio_path),
             "-c:v", "libx264", "-crf", str(jesucitos.CRF), "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", *extra_args,
             "-shortest", str(salida)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
        )
        assert proceso.stdin is not None
        try:
            for i in range(n_frames):
                t = i / jesucitos.FPS
                personaje = jesucitos.dibujar_personaje(
                    apertura=aperturas[i], parpadeo=parpadeo[i],
                    brazo=0.0, bob=0.0, halo=0.7,
                )
                proceso.stdin.write(jesucitos.componer_frame(fondo, personaje, t, duracion))
        finally:
            proceso.stdin.close()
            codigo = proceso.wait()

        log.seek(0)
        crudo = log.read()

    return {
        # `str` y no el Path: `dbutils.notebook.exit` solo serializa JSON y
        # un PosixPath tumbaba la tarea justo después de haber renders hechos.
        "destino": str(destino),
        "frames": n_frames,
        "codigo": codigo,
        "bytes": salida.stat().st_size if salida.exists() else 0,
        "errores": lineas_de_error(crudo)[:25],
    }


# COMMAND ----------

for etiqueta, destino, extra in [
    ("volumen", tmp / "volumen.mp4", ()),
    ("tmp", "/tmp/probe_render.mp4", ()),
    ("volumen_fragmentado", tmp / "fragmentado.mp4",
     ("-movflags", "+frag_keyframe+empty_moov+default_base_moof")),
]:
    try:
        resultado[etiqueta] = render(destino, extra)
    except Exception as exc:
        resultado[etiqueta] = f"ERROR {type(exc).__name__}: {exc}"
        resultado[etiqueta + "_traza"] = traceback.format_exc()[-600:]

# COMMAND ----------

# A la vez que la salida del notebook, se deja en el volumen: si la tarea muere
# después del render (como pasó con el TypeError), el diagnóstico no se pierde.
salida_json = json.dumps(resultado, ensure_ascii=False, indent=2)
Path(VIDEOS_DIR, "probe_render_resultado.json").write_text(salida_json, encoding="utf-8")
dbutils.notebook.exit(salida_json)
