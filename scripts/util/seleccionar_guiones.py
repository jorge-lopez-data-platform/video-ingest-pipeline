# Databricks notebook source
# MAGIC %md
# MAGIC # Selecciona el lote y prepara el manifiesto de producción
# MAGIC
# MAGIC Fase **serial** del job: se ejecuta una sola vez, antes de repartir el
# MAGIC trabajo entre las iteraciones paralelas de `generar_video`.
# MAGIC
# MAGIC Aquí se hacen las tres cosas que no se pueden hacer en paralelo porque
# MAGIC dependen de lo elegido en el mismo lote:
# MAGIC
# MAGIC 1. **Elegir los guiones.** En paralelo, cada iteración elegiría el mismo
# MAGIC    "mejor" guion y se produciría tres veces el mismo video.
# MAGIC 2. **Diseñar las fichas del personaje.** El LLM recibe las caras ya usadas
# MAGIC    para no repetir. Son ~2-3 s por ficha; paralelizarlas no compensa y
# MAGIC    rompería la garantía de que dos videos del lote no compartan cara.
# MAGIC 3. **Sortear los hashtags.** El sorteo excluye lo ya asignado en la corrida;
# MAGIC    con N procesos aislados, dos videos podían salir con los mismos.
# MAGIC
# MAGIC El resultado se escribe en un **manifiesto** JSON en el volumen y se publica
# MAGIC como `taskValues` para que `for_each_task` lance una iteración por guion.
# MAGIC El manifiesto lleva el guion completo y la ficha ya decidida: el render no
# MAGIC necesita volver a consultar la tabla ni al LLM, y así cada video es
# MAGIC reproducible a partir de su entrada en el manifiesto.
# MAGIC
# MAGIC No registra nada en `guiones_usados`: eso lo hace cada render al terminar,
# MAGIC de modo que un video fallido no quema su guion.

# COMMAND ----------

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

dbutils.widgets.text("video_fuente_id", "", "Vacío = elige los mejores guiones de la tabla")
dbutils.widgets.text("excluir_ids", "", "IDs ya usados, separados por coma")
dbutils.widgets.text("cantidad", "3", "Cuántos videos distintos generar")
dbutils.widgets.dropdown("personaje", "motivacional", ["motivacional", "jesucitos"])
dbutils.widgets.text("llm_model", "deepseek/deepseek-v4-pro-0813")
dbutils.widgets.dropdown("solo_sin_verificar", "false", ["false", "true"])
dbutils.widgets.text("temas_hashtags", "", "Temas del catálogo de hashtags, separados por coma")
dbutils.widgets.text("n_hashtags", "5", "Cuántos hashtags por video")
dbutils.widgets.text("regenerar_ids", "",
                     "IDs a regenerar aunque ya estén en el registro de usados")
dbutils.widgets.dropdown("reusar_personaje", "false", ["false", "true"])
dbutils.widgets.text("sufijo", "", "Sufijo del mp4 (p. ej. _v2); vacío = sobrescribe")
dbutils.widgets.dropdown("desde_historial", "false", ["false", "true"],
                         "Leer también las versiones antiguas de la tabla")
# Los de audio no se usan aquí: se copian tal cual al manifiesto para que el
# render los lea de un único sitio y no de dos fuentes que puedan discrepar.
dbutils.widgets.text("voz", "es-MX-JorgeNeural")
dbutils.widgets.text("rate", "+8%")
dbutils.widgets.text("marca_usuario", "@alguienqueteescucha",
                     "Marca de agua: arroba del canal (vacío = sin marca)")

LIB = "/Volumes/tiktok/default/videos/lib"
VIDEOS_DIR = "/Volumes/tiktok/default/videos"

sys.path.insert(0, LIB)
from scripts import seleccion  # noqa: E402

print("Librería importada desde", LIB)

# COMMAND ----------

# MAGIC %md ## 1. Elegir los guiones

# COMMAND ----------

sin_verificar = dbutils.widgets.get("solo_sin_verificar").strip().lower() == "true"
pedido = dbutils.widgets.get("video_fuente_id").strip()
cantidad = max(1, int(dbutils.widgets.get("cantidad") or 1))
excluir = {i.strip() for i in dbutils.widgets.get("excluir_ids").split(",") if i.strip()}

# Regenerar es una excepción deliberada: se pide un video concreto que ya existe
# y el registro de usados, que existe para no repetir contenido, aquí estorbaría.
regenerar = {i.strip() for i in dbutils.widgets.get("regenerar_ids").split(",") if i.strip()}
if regenerar:
    print(f"Regeneración explícita de {len(regenerar)} IDs: {sorted(regenerar)}")
excluir |= seleccion.ids_ya_usados(spark)
excluir -= regenerar
print(f"Excluidos (registro + widget): {len(excluir)}")

filas = seleccion.leer_guiones(
    spark, pedido, dbutils.widgets.get("desde_historial").strip().lower() == "true")
elegidos = seleccion.elegir(filas, excluir, cantidad, sin_verificar, pedido)

print(f"Guiones elegidos: {[str(g['video_fuente_id']) for g in elegidos]}")
for g in elegidos:
    print(f"  {g['video_fuente_id']} | confianza={g.get('confianza')} | "
          f"palabras={g.get('palabras_reales')} | "
          f"verificar={g.get('datos_a_verificar') or '(nada)'}")

# COMMAND ----------

# MAGIC %md ## 2. Diseñar la ficha del personaje de cada guion
# MAGIC
# MAGIC `fichas_previas` se amplía tras cada diseño, así que los tres videos del
# MAGIC lote tampoco repiten cara entre sí además de no repetir las del historial.

# COMMAND ----------

personaje = dbutils.widgets.get("personaje").strip() or "motivacional"
reusar = dbutils.widgets.get("reusar_personaje").strip().lower() == "true"

# El cliente se crea aquí y no dentro del bloque del personaje: los títulos
# también lo necesitan, y con el personaje por defecto (jesucitos) ese bloque no
# llega a ejecutarse.
llm = seleccion.crear_cliente(dbutils)
modelo = dbutils.widgets.get("llm_model").strip()

fichas = {}
if personaje == "motivacional":
    from scripts import personaje_llm
    from scripts.personaje import spec_normalizada

    fichas_previas, fichas_por_id = seleccion.cargar_fichas(spark)
    print(f"Fichas previas en el historial: {len(fichas_previas)}")

    for guion in elegidos:
        vid = str(guion["video_fuente_id"])
        guardada = fichas_por_id.get(vid) if reusar else None
        if guardada:
            # Las fichas antiguas se guardaron antes de que existiera el eje
            # `escenario`; sin normalizar, el render falla con KeyError.
            ficha = spec_normalizada(guardada)
            print(f"  {vid}: se reutiliza la ficha guardada (misma cara)")
        else:
            ficha = personaje_llm.disenar(llm, modelo, guion, fichas_previas)
            print(f"  {vid}: pelo={ficha['pelo_estilo']} ropa={ficha['ropa_tipo']} "
                  f"{ficha['ropa_color']} acc={ficha['accesorio']}")
        fichas_previas.append(ficha)
        fichas[vid] = ficha
        print(f"      emoción={ficha.get('expresion')} | escenario={ficha.get('escenario')}")

# COMMAND ----------

# MAGIC %md ## 3. Sortear los hashtags del lote

# COMMAND ----------

try:
    N_HASHTAGS = max(1, int(dbutils.widgets.get("n_hashtags") or 5))
except ValueError:
    N_HASHTAGS = 5
    print("n_hashtags inválido; se usa 5.")

TEMAS = ([t.strip() for t in dbutils.widgets.get("temas_hashtags").split(",") if t.strip()]
         or ["motivacional"])

ids = [str(g["video_fuente_id"]) for g in elegidos]
ht_por_guion = seleccion.sortear_para(spark, TEMAS, N_HASHTAGS, ids)

# COMMAND ----------

# MAGIC %md ## 3b. Elegir un título distinto para cada video
# MAGIC
# MAGIC `texto_pantalla_1` del guion servía de titular, pero el LLM a veces copiaba
# MAGIC el placeholder del esquema ("overlay 0-5: …") o repetía el hook literal, y
# MAGIC nada impedía que dos videos salieran con el mismo título.
# MAGIC
# MAGIC Se decide aquí, en la fase serial, igual que los hashtags y las caras: si
# MAGIC cada iteración paralela eligiera el suyo, no habría forma de garantizar que
# MAGIC fueran distintos entre sí. El título viaja en el manifiesto y lo escribe en
# MAGIC el `.txt` la tarea de render.

# COMMAND ----------

from scripts import titulos  # noqa: E402

titulos_previos = titulos.cargar_historial(spark)
print(f"Títulos en el historial: {len(titulos_previos)}")

ya_usados = list(titulos_previos)
titulo_por_guion = {}
for guion in elegidos:
    vid = str(guion["video_fuente_id"])
    titulo_por_guion[vid] = titulos.elegir(llm, modelo, guion, evitar=ya_usados)
    ya_usados.append(titulo_por_guion[vid])
    print(f"  {vid}: {titulo_por_guion[vid]}")

# COMMAND ----------

# MAGIC %md ## 4. Escribir el manifiesto del lote
# MAGIC
# MAGIC El nombre lleva marca de tiempo porque cada corrida produce un lote nuevo:
# MAGIC sobrescribir un único `manifiesto.json` haría que dos corridas solapadas
# MAGIC se pisaran y un render leería la ficha de otro guion.

# COMMAND ----------

sufijo = dbutils.widgets.get("sufijo").strip()
prefijo = "motivacional" if personaje == "motivacional" else "jesucitos"
marca_instante = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

carpeta_lotes = Path(VIDEOS_DIR) / "lotes"
carpeta_lotes.mkdir(parents=True, exist_ok=True)
manifiesto = carpeta_lotes / f"lote_{marca_instante}.json"

videos = []
for guion in elegidos:
    vid = str(guion["video_fuente_id"])
    videos.append({
        "video_fuente_id": vid,
        # El nombre es predecible a propósito: la tarea que empaqueta y la que
        # sube necesitan saber qué archivo esperar sin volver a mirar el disco.
        "video_name": f"{prefijo}_{vid}{sufijo}.mp4",
        "hashtags": ",".join(ht_por_guion.get(vid, [])),
        # El título va decidido desde aquí: es lo que garantiza que el lote
        # entero no repita, cosa imposible de asegurar desde las iteraciones.
        "titulo": titulo_por_guion[vid],
        "spec": fichas.get(vid),
        "guion": guion,
    })

manifiesto.write_text(
    json.dumps(
        {
            "creado": marca_instante,
            "personaje": personaje,
            "prefijo": prefijo,
            "sufijo": sufijo,
            "llm_model": dbutils.widgets.get("llm_model").strip(),
            "audio": {
                "voz": dbutils.widgets.get("voz").strip(),
                "rate": dbutils.widgets.get("rate").strip(),
                "marca_usuario": dbutils.widgets.get("marca_usuario"),
            },
            "videos": videos,
        },
        ensure_ascii=False, indent=1, default=str,
    ),
    encoding="utf-8",
)
print(f"Manifiesto: {manifiesto} ({len(videos)} guiones)")

# `inputs` de `for_each_task` solo necesita el ID; el resto de la entrada la lee
# cada iteración del manifiesto. Mantener `inputs` mínimo evita acercarse al
# límite de tamaño de los task values.
dbutils.jobs.taskValues.set(key="manifiesto", value=str(manifiesto))
# `ids` es una lista de CADENAS, no de diccionarios, y el for_each la consume con
# `{{input}}`. Con `[{"video_fuente_id": ...}]` y `{{input.video_fuente_id}}` la
# sustitucion llegaba vacia en produccion (las iteraciones morian con "El ID ''
# no esta en el manifiesto"), aunque en un job de prueba minimo si funcionara.
# La forma plana elimina toda ambiguedad de desreferencia y esta verificada.
dbutils.jobs.taskValues.set(
    key="ids", value=[str(v["video_fuente_id"]) for v in videos])
# `{{input}}` de un for_each NO se sustituye sobre esta lista: el 2026-10-25 las
# tres iteraciones de `generar_video` recibieron `''` y murieron con "El ID ''
# no está en el manifiesto". Se publica también la lista de posiciones, y
# `render_video_lote` resuelve el ID leyendo el manifiesto por índice.
dbutils.jobs.taskValues.set(
    key="indices", value=[str(i) for i in range(len(videos))])
dbutils.jobs.taskValues.set(
    key="videos", value=[{"video_name": v["video_name"],
                          "video_fuente_id": v["video_fuente_id"]} for v in videos])
# `videos` lo lee `pendientes_borradores` con `taskValues.get` desde Python, y
# eso funciona. Pero como `inputs` de un for_each la lista de diccionarios no se
# desreferencia: `publicar_youtube` recibia `video_name` nulo. Para los for_each
# se publica aparte la lista plana de nombres.
dbutils.jobs.taskValues.set(
    key="nombres", value=[str(v["video_name"]) for v in videos])
print(f"IDs publicados para el for_each: {[v['video_fuente_id'] for v in videos]}")
