# Databricks notebook source
# MAGIC %md
# MAGIC # Renderiza UN video del lote
# MAGIC
# MAGIC Esta tarea es la que se ejecuta **en paralelo**: `for_each_task` la lanza
# MAGIC una vez por guion con `concurrency > 1`, y cada iteración recibe su propio
# MAGIC compute. Todo lo que depende del lote entero (qué guiones, qué cara, qué
# MAGIC hashtags) ya lo decidió `seleccionar_guiones` y está en el manifiesto.
# MAGIC
# MAGIC Por eso aquí no hay `for` sobre guiones y no se consulta el LLM: la entrada
# MAGIC es una sola fila del manifiesto, y el render es reproducible a partir de
# MAGIC ella.
# MAGIC
# MAGIC **Lo que sí se escribe aquí y no en la tarea serial** son los registros de
# MAGIC `guiones_usados` / `personajes_usados` / `hashtags_usados`: se apuntan justo
# MAGIC cuando el mp4 ya existe. Si se apuntaran en la fase serial, un render
# MAGIC fallido quemaría el guion y ese video no se volvería a producir nunca.
# MAGIC Cada iteración escribe **solo su fila**, así que no hay contención.

# COMMAND ----------

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import edge_tts

dbutils.widgets.text("manifiesto", "", "Ruta del manifiesto del lote (task value)")
# El widget NO se llama `video_fuente_id` aunque contenga justamente eso: el job
# tiene un job parameter con ese nombre, y cuando el `base_parameter` de la
# iteración se llama igual que un job parameter, Databricks resuelve la
# sustitución contra el parámetro del job (default "") en vez de contra
# `{{input}}`. Verificado el 2026-10-25 con un job A/B: mismo `{{input}}`,
# `video_fuente_id` -> vacío, `id_lote` -> el valor correcto.
dbutils.widgets.text("id_lote", "", "ID del guion que renderiza esta iteración")
dbutils.widgets.dropdown("dry_run", "false", ["false", "true"])

LIB = "/Volumes/tiktok/default/videos/lib"
VIDEOS_DIR = "/Volumes/tiktok/default/videos"
FONDO = f"{VIDEOS_DIR}/assets/paraiso_ia.png"
TABLA_USADOS = "tiktok.gold.guiones_usados"
TABLA_PERSONAJES = "tiktok.gold.personajes_usados"
TABLA_HASHTAGS = "tiktok.gold.hashtags_usados"

sys.path.insert(0, LIB)
from scripts.util import jesucitos, subtitular, common, rutas, titulos  # noqa: E402
from scripts.util.personaje import firma as personaje_firma  # noqa: E402
from scripts.util.tts import texto_del_guion  # noqa: E402

print("Librería importada desde", LIB)

# COMMAND ----------

# MAGIC %md ## 1. Leer la entrada del manifiesto

# COMMAND ----------

ruta_manifiesto = dbutils.widgets.get("manifiesto").strip()
fuente_id = dbutils.widgets.get("id_lote").strip()
dry_run = dbutils.widgets.get("dry_run").strip().lower() == "true"

if not ruta_manifiesto:
    raise ValueError("Falta el parámetro `manifiesto`: lo publica la tarea seleccionar_guiones.")
if not Path(ruta_manifiesto).is_file():
    raise FileNotFoundError(f"No existe el manifiesto {ruta_manifiesto}")

lote = json.loads(Path(ruta_manifiesto).read_text(encoding="utf-8"))
por_id = {str(v["video_fuente_id"]): v for v in lote["videos"]}

if fuente_id not in por_id:
    raise ValueError(f"El ID {fuente_id!r} no está en el manifiesto {ruta_manifiesto}. "
                     f"IDs: {sorted(por_id)}")

entrada = por_id[fuente_id]
guion = entrada["guion"]
hashtags = entrada.get("hashtags") or ""
# El título lo eligió la fase serial para que no se repita en el lote. Si el
# manifiesto es de una corrida anterior a este campo, se cae al de siempre.
titulo = (entrada.get("titulo") or "").strip() or titulos.normalizar(
    guion.get("texto_pantalla_1") or guion.get("hook"))
ficha = entrada.get("spec")
personaje = lote["personaje"]
prefijo = lote["prefijo"]
voz = lote["audio"]["voz"] or "es-MX-JorgeNeural"
rate = lote["audio"]["rate"] or "+8%"
marca_usuario = lote["audio"].get("marca_usuario") or ""

print("=" * 60)
print(f"GUION {fuente_id} (manifiesto {Path(ruta_manifiesto).name})")
if ficha:
    print(f"  personaje: pelo={ficha['pelo_estilo']} ropa={ficha['ropa_tipo']} "
          f"{ficha['ropa_color']} acc={ficha['accesorio']}")
    print(f"  emoción: {ficha['expresion']} | escenario: {ficha['escenario']}")
print(f"  hashtags: {hashtags}")

# COMMAND ----------

# MAGIC %md ## 2. Voz con tiempos por palabra
# MAGIC
# MAGIC `boundary="WordBoundary"` es obligatorio: por defecto edge-tts agrupa por
# MAGIC frase y no habría de dónde sacar el tiempo de cada palabra.

# COMMAND ----------


async def sintetizar_con_tiempos(texto, voz, rate):
    """Devuelve (bytes mp3, palabras con inicio/fin)."""
    comunicador = edge_tts.Communicate(texto, voz, rate=rate, boundary="WordBoundary")
    audio = bytearray()
    palabras = []
    async for trozo in comunicador.stream():
        if trozo["type"] == "audio":
            audio.extend(trozo["data"])
        elif trozo["type"] == "WordBoundary":
            palabras.append({
                "texto": trozo["text"],
                "inicio": round(trozo["offset"] / 1e7, 3),
                "fin": round((trozo["offset"] + trozo["duration"]) / 1e7, 3),
            })
    return bytes(audio), palabras


def ass_con_titulo(ass_texto, titulo, duracion):
    """Añade el título arriba, en un estilo aparte.

    Alignment 8 = arriba centrado, lejos de la interfaz de TikTok. El título
    existe porque el endpoint de borrador no acepta `post_info`: si no se
    quema, nunca llega a TikTok.
    """
    if not titulo:
        return ass_texto
    estilo = (
        "Style: TikTokTitulo,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "1,0,0,0,100,100,0,0,1,5,2,8,60,60,240,1\n"
    )
    dialogo = (
        f"Dialogue: 1,{subtitular._timestamp(0)},"
        f"{subtitular._timestamp(min(4.0, duracion))},TikTokTitulo,,0,0,0,,{titulo}"
    )
    con_estilo = ass_texto.replace("[Events]", estilo + "\n[Events]")
    return con_estilo.rstrip("\n") + "\n" + dialogo + "\n"


texto = texto_del_guion(guion)
if not texto.strip():
    raise ValueError(f"El guion {fuente_id} no tiene texto")

# Los temporales van al volumen y no a `/tmp`: el disco efímero del serverless se
# llenó a mitad de un render de 70 s (ffmpeg falló al escribir el átomo `moov`).
# La carpeta lleva el ID porque en un lote paralelo conviven varios renders.
tmp = Path(tempfile.mkdtemp(prefix=f"video_guion_{fuente_id}_", dir=VIDEOS_DIR))
carpeta = tmp / fuente_id
carpeta.mkdir(parents=True, exist_ok=True)
audio_path = carpeta / "voz.mp3"

datos, palabras = await sintetizar_con_tiempos(texto, voz, rate)
if not datos or not palabras:
    raise RuntimeError("edge-tts no devolvió audio o tiempos por palabra")

audio_path.write_bytes(datos)
duracion = common.probe(audio_path)["duracion"]
print(f"  audio: {duracion:.1f}s, {len(palabras)} palabras con tiempo")

# COMMAND ----------

# MAGIC %md ## 3. Animación, subtítulos y mp4

# COMMAND ----------

nombre = entrada["video_name"]
if dry_run:
    print("  dry_run: se detiene antes del render")
else:
    sin_subs = carpeta / "video.mp4"
    # El fondo por defecto solo se usa con el personaje original. Con
    # `motivacional` el escenario lo eligió el LLM en la fase serial y viene en
    # la ficha; pasar la imagen fija haría que el eje "escenario" no se viera.
    if personaje == "motivacional":
        fondo_ruta = None
        escenario = ficha["escenario"] if ficha else "paraiso"
    else:
        fondo_ruta = Path(FONDO) if os.path.isfile(FONDO) else None
        escenario = "paraiso"
    jesucitos.animar(audio_path, fondo_ruta, sin_subs,
                     personaje=personaje, spec=ficha, escenario=escenario)

    chunks = subtitular.agrupar(palabras)
    ass_texto = ass_con_titulo(subtitular.generar_ass(chunks), titulo, duracion)
    ass_texto = subtitular.agregar_marca(ass_texto, duracion, marca_usuario)
    ass_path = carpeta / "subs.ass"
    ass_path.write_text(ass_texto, encoding="utf-8")

    # Agrupado por fecha: el lote queda en videos/AAAA/MM/DD/ para no mezclarse
    # con los renders de otros días al descargarlo.
    salida = Path(rutas.carpeta_del_dia(VIDEOS_DIR)) / nombre
    salida.parent.mkdir(parents=True, exist_ok=True)
    subtitular.quemar(sin_subs, ass_path, salida)

    # Los hashtags del guion se sustituyen por los sorteados en la fase serial:
    # el guion repite siempre los mismos y aquí se busca variedad entre videos.
    publicacion = salida.with_name(f"{Path(nombre).stem}_publicacion.txt")
    publicacion.write_text(
        f"{titulo}\n\n" + " ".join(
            "#" + h.strip().lstrip("#") for h in hashtags.split(",") if h.strip()
        ),
        encoding="utf-8",
    )

    info = common.probe(salida)
    print(f"  ✅ {nombre} ({info['duracion']:.1f}s, {info['ancho']}x{info['alto']}, "
          f"{salida.stat().st_size / 1e6:.1f} MB, {len(chunks)} subtítulos)")
    print(f"  título: {titulo}")

# Los intermedios pesan ~17 MB por video y ya no hacen falta: sin esto, cada
# corrida dejaría basura acumulándose en el volumen. Va antes de los registros
# para que un fallo al escribir en Delta no deje la carpeta temporal huérfana.
shutil.rmtree(tmp, ignore_errors=True)

# COMMAND ----------

# MAGIC %md ## 4. Registrar este video en las tablas de usados
# MAGIC
# MAGIC Se escribe solo la fila de esta iteración: en paralelo, cualquier
# MAGIC "leer, modificar, escribir" sobre la tabla compartida sería una carrera.

# COMMAND ----------

if dry_run:
    print("dry_run: no se registra nada")
else:
    from pyspark.sql import Row
    from pyspark.sql.types import StringType, StructField, StructType, TimestampType

    ahora = datetime.now(timezone.utc)

    spark.sql(
        f"""CREATE TABLE IF NOT EXISTS {TABLA_USADOS} (
              video_fuente_id STRING, video_name STRING, generado_en TIMESTAMP
            ) USING DELTA"""
    )
    existente = {str(r["video_fuente_id"]) for r in spark.sql(
        f"SELECT video_fuente_id FROM {TABLA_USADOS} "
        f"WHERE video_fuente_id = '{fuente_id}'").collect()}
    if not existente:
        esquema = StructType([
            StructField("video_fuente_id", StringType()),
            StructField("video_name", StringType()),
            StructField("generado_en", TimestampType()),
        ])
        spark.createDataFrame(
            [Row(video_fuente_id=fuente_id, video_name=nombre, generado_en=ahora)],
            esquema).write.mode("append").saveAsTable(TABLA_USADOS)
        print(f"  registrado en {TABLA_USADOS}")
    else:
        # Regenerar un ID ya registrado duplicaría la fila sin aportar nada: el
        # registro dice "qué guion ya se usó", no "cuántas veces".
        print(f"  {fuente_id} ya estaba en {TABLA_USADOS}")

    if personaje == "motivacional" and ficha:
        spark.sql(
            f"""CREATE TABLE IF NOT EXISTS {TABLA_PERSONAJES} (
                  video_fuente_id STRING, firma STRING, spec STRING, generado_en TIMESTAMP
                ) USING DELTA"""
        )
        esquema_pj = StructType([
            StructField("video_fuente_id", StringType()),
            StructField("firma", StringType()),
            StructField("spec", StringType()),
            StructField("generado_en", TimestampType()),
        ])
        spark.createDataFrame(
            [Row(video_fuente_id=fuente_id, firma=personaje_firma(ficha),
                 spec=json.dumps(ficha, ensure_ascii=False), generado_en=ahora)],
            esquema_pj).write.mode("append").saveAsTable(TABLA_PERSONAJES)
        print(f"  registrado en {TABLA_PERSONAJES}")

    # El historial de hashtags es lo que alimenta el sorteo LRU de la próxima
    # corrida; sin esta fila, el reparto no recuerda lo que ya salió.
    try:
        spark.sql(
            f"""CREATE TABLE IF NOT EXISTS {TABLA_HASHTAGS} (
                  video_fuente_id STRING, video_name STRING, hashtags STRING,
                  generado_en TIMESTAMP
                ) USING DELTA"""
        )
        esquema_ht = StructType([
            StructField("video_fuente_id", StringType()),
            StructField("video_name", StringType()),
            StructField("hashtags", StringType()),
            StructField("generado_en", TimestampType()),
        ])
        spark.createDataFrame(
            [Row(video_fuente_id=fuente_id, video_name=nombre,
                 hashtags=hashtags, generado_en=ahora)],
            esquema_ht).write.mode("append").saveAsTable(TABLA_HASHTAGS)
        print(f"  registrado en {TABLA_HASHTAGS}")
    except Exception as exc:
        print(f"  AVISO: no se pudo registrar el historial de hashtags "
              f"({type(exc).__name__}): {exc}")

    # El historial de títulos es lo que impide repetir titular en la próxima
    # corrida. Se apunta aquí, con el mp4 ya en disco, por la misma razón que
    # `guiones_usados`: un render fallido no debe gastar el título.
    #
    # La tabla la crea `titulos.registrar`, no este bloque. Por eso la consulta
    # de duplicados NO puede ir antes del primer registro sin protegerse: si la
    # tabla no existe todavía (primera corrida), el SELECT lanzaba
    # AnalysisException y el `except` se tragaba también el registro, de modo
    # que la tabla no se creaba nunca. Pasó desapercibido porque el aviso no
    # tumba el render. `cargar_historial` ya devuelve [] si la tabla falta.
    try:
        ya = {str(r["video_fuente_id"]) for r in spark.sql(
            f"SELECT video_fuente_id FROM {titulos.TABLA_TITULOS} "
            f"WHERE video_fuente_id = '{fuente_id}'").collect()}
    except Exception:
        ya = set()
    if ya:
        print(f"  {fuente_id} ya tenía título registrado")
    else:
        try:
            titulos.registrar(spark, fuente_id, titulo)
            print(f"  registrado en {titulos.TABLA_TITULOS}")
        except Exception as exc:
            print(f"  AVISO: no se pudo registrar el título "
                  f"({type(exc).__name__}): {exc}")

    dbutils.jobs.taskValues.set(
        key="videos",
        value=[{"video_name": nombre, "video_fuente_id": fuente_id}])
    print(f"  publicado para la tarea de subida")
