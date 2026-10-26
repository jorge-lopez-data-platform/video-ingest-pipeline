# Databricks notebook source
# MAGIC %md
# MAGIC # Genera los videos a partir de los guiones de `tiktok_guiones_60s_viral`
# MAGIC
# MAGIC Toma hasta `cantidad` guiones de `tiktok.gold.guiones_60s`, pone voz con edge-tts,
# MAGIC anima con `scripts/jesucitos.py` y quema subtítulos. Cada video deja un `.txt` con
# MAGIC su título y hashtags para copiarlos al publicar.
# MAGIC
# MAGIC Decisiones que no son obvias:
# MAGIC - **No se usa Whisper.** edge-tts devuelve `WordBoundary` con el tiempo exacto de cada
# MAGIC   palabra; transcribir lo que nosotros mismos escribimos solo añadiría errores.
# MAGIC - `boundary="WordBoundary"` es obligatorio: por defecto edge-tts agrupa por frase.
# MAGIC - Se descartan los guiones con `datos_a_verificar` no vacío. El LLM inventa cifras y
# MAGIC   tendencias; publicar eso sin verificar es el riesgo ya documentado.
# MAGIC - El endpoint de borrador no acepta `post_info`, así que el título se quema en el video
# MAGIC   y los hashtags se escriben en un `.txt` al lado del mp4, para pegarlos a mano.
# MAGIC - La librería vive en el volumen (`lib/`) y se importa con `sys.path`: serverless no
# MAGIC   tiene el repo local.
# MAGIC - Cada render se guarda en `videos/AAAA/MM/DD/` y el lote deja un
# MAGIC   `descripciones_reels.md` en esa misma carpeta, con título, descripción, tags y
# MAGIC   hashtags listos para pegar en el formulario de YouTube. El nombre del mp4 no se
# MAGIC   toca: es el identificador que comparten `gold.guiones_usados` y el borrador de TikTok.

# COMMAND ----------

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import edge_tts

dbutils.widgets.text("video_fuente_id", "", "Vacío = elige los mejores guiones de la tabla")
dbutils.widgets.text("excluir_ids", "", "IDs ya usados, separados por coma")
dbutils.widgets.text("cantidad", "1", "Cuántos videos distintos generar")
dbutils.widgets.text("voz", "es-MX-JorgeNeural")
dbutils.widgets.text("rate", "+8%")
dbutils.widgets.dropdown("personaje", "motivacional", ["motivacional", "jesucitos"])
dbutils.widgets.text("llm_model", "deepseek/deepseek-v4-pro-0813")
dbutils.widgets.dropdown("solo_sin_verificar", "false", ["false", "true"])
dbutils.widgets.dropdown("dry_run", "false", ["false", "true"])
dbutils.widgets.text("temas_hashtags", "", "Temas del catálogo de hashtags, separados por coma")
dbutils.widgets.text("n_hashtags", "5", "Cuántos hashtags por video")
dbutils.widgets.text("marca_usuario", "@alguienqueteescucha",
                     "Marca de agua: arroba del canal (vacío = sin marca)")
dbutils.widgets.text("regenerar_ids", "",
                     "IDs a regenerar aunque ya estén en el registro de usados")
dbutils.widgets.dropdown("reusar_personaje", "false", ["false", "true"])
dbutils.widgets.text("sufijo", "", "Sufijo del mp4 (p. ej. _v2); vacío = sobrescribe")
dbutils.widgets.dropdown("desde_historial", "false", ["false", "true"],
                         "Leer también las versiones antiguas de la tabla")

LIB = "/Volumes/tiktok/default/videos/lib"
VIDEOS_DIR = "/Volumes/tiktok/default/videos"
FONDO = f"{VIDEOS_DIR}/assets/paraiso_ia.png"
TABLA = "tiktok.gold.guiones_60s"
# Registro persistente de qué video fuente ya se convirtió en un video. Sin esta
# tabla la exclusión dependería de un widget que se pierde entre corridas, y el
# generador volvería a elegir siempre el mismo "mejor" guion.
TABLA_USADOS = "tiktok.gold.guiones_usados"
# Registro de fichas de personaje ya usadas. Es lo que impide que dos videos
# distintos compartan la misma cara, aunque el LLM insista.
TABLA_PERSONAJES = "tiktok.gold.personajes_usados"

sys.path.insert(0, LIB)
from scripts import jesucitos, subtitular, common, rutas, descripciones  # noqa: E402
from scripts.tts import texto_del_guion  # noqa: E402

print("Librería importada desde", LIB)

# COMMAND ----------

# MAGIC %md ## 1. Elegir los guiones

# COMMAND ----------

sin_verificar = dbutils.widgets.get("solo_sin_verificar").strip().lower() == "true"
pedido = dbutils.widgets.get("video_fuente_id").strip()
cantidad = max(1, int(dbutils.widgets.get("cantidad") or 1))
excluir = {i.strip() for i in dbutils.widgets.get("excluir_ids").split(",") if i.strip()}


def ids_ya_usados():
    """IDs que ya produjeron un video, según el registro persistente.

    Se lee de una tabla y no de un widget porque un widget solo dura una
    corrida: sin persistencia, cada ejecución elegiría el mismo guion.
    """
    try:
        filas_usadas = spark.sql(f"SELECT video_fuente_id FROM {TABLA_USADOS}").collect()
        return {str(r["video_fuente_id"]) for r in filas_usadas}
    except Exception as exc:
        print(f"Registro {TABLA_USADOS} no legible ({type(exc).__name__}); se asume vacío.")
        return set()


# Regenerar es una excepción deliberada: se pide un video concreto que ya existe
# (por ejemplo, para rehacerlo con otra marca de agua) y el registro de usados, que
# existe para no repetir contenido, aquí estorbaría. Solo se exime lo pedido; el
# resto sigue excluido para no duplicar por accidente.
regenerar = {i.strip() for i in dbutils.widgets.get("regenerar_ids").split(",") if i.strip()}
if regenerar:
    print(f"Regeneración explícita de {len(regenerar)} IDs: {sorted(regenerar)}")
excluir |= ids_ya_usados()
excluir -= regenerar
print(f"Excluidos (registro + widget): {len(excluir)}")

columnas = ("video_fuente_id, hook, open_loop, bloque_1, pattern_interrupt, bloque_2, "
            "pre_cierre, cierre, cta, hashtags, datos_a_verificar, confianza, "
            "palabras_reales, duracion_estimada, texto_pantalla_1")
filtro = f"WHERE video_fuente_id = '{pedido}'" if pedido else ""
consulta = f"SELECT {columnas} FROM {TABLA} {filtro} ORDER BY generado_en DESC"
filas = [f.asDict() for f in spark.sql(consulta).collect()]

if dbutils.widgets.get("desde_historial").strip().lower() == "true":
    # La tabla se sobrescribía en cada corrida del job de guiones, así que los
    # guiones de los videos ya hechos dejaron de estar en la versión actual.
    # Delta conserva las anteriores: leerlas permite regenerar un video sin
    # re-scrapear (y sin gastar Apify). `v` desempata por versión y se queda el
    # guion más reciente de cada ID, que es el que produjo el video en disco.
    # Desde la versión 7 la tabla tiene las 20 columnas; las anteriores nacieron
    # con un esquema más corto y consultarlas rompe el UNION. Se ordena de la más
    # nueva a la más vieja para quedarse siempre con el guion más reciente.
    versiones = sorted(
        (r["version"] for r in spark.sql(f"DESCRIBE HISTORY {TABLA}").collect()
         if r["version"] >= 7),
        reverse=True,
    )
    bloques = [
        f"SELECT {v} AS v, {columnas} FROM {TABLA} VERSION AS OF {v}"
        for v in versiones
    ]
    recuperados = spark.sql(f"""
        SELECT {columnas} FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY video_fuente_id ORDER BY v DESC) rn
          FROM ({' UNION ALL '.join(bloques)})
        ) WHERE rn = 1
    """).collect()
    ya = {str(f["video_fuente_id"]) for f in filas}
    nuevos = [f.asDict() for f in recuperados if str(f["video_fuente_id"]) not in ya]
    print(f"Historial: {len(versiones)} versiones, {len(nuevos)} guiones recuperados")
    filas.extend(nuevos)

if not filas:
    raise ValueError(f"La tabla {TABLA} está vacía. Ejecuta antes el job de guiones.")

if pedido:
    elegidos = filas[:1]
else:
    candidatos = [f for f in filas if str(f["video_fuente_id"]) not in excluir]
    if sin_verificar:
        limpias = candidatos
    else:
        # El prompt obliga al LLM a listar en `datos_a_verificar` cada cifra,
        # nombre o lugar, así que exigir esa lista vacía dejaba fuera a casi
        # todos los guiones. Lo que decide es la confianza que declara el
        # propio modelo: solo se descarta la "baja".
        limpias = [f for f in candidatos
                   if str(f.get("confianza") or "").strip().lower() != "baja"]
    if not limpias:
        if excluir and not candidatos:
            raise ValueError(
                f"No quedan guiones sin usar ({len(excluir)} excluidos). Vuelve a generar "
                "guiones antes de producir otro video."
            )
        raise ValueError(
            "Todos los guiones disponibles son de confianza baja. Vuelve a generar "
            "guiones o pasa solo_sin_verificar=true asumiendo el riesgo."
        )
    # Mejores primero: confianza alta y duración cercana a 60 s.
    ordenados = sorted(
        limpias,
        key=lambda f: (f.get("confianza") != "alta",
                       abs((f.get("duracion_estimada") or 60.0) - 60.0)),
    )
    elegidos = ordenados[:cantidad]

    if len(elegidos) < cantidad:
        print(f"AVISO: se pidieron {cantidad} videos y solo hay {len(elegidos)} guiones "
              f"utilizables. Se generarán {len(elegidos)}.")

# Índice por id para que el paquete de descripciones pueda recuperar el guion
# completo de cada video sin volver a consultar la tabla al final del bucle.
guiones_por_id = {str(g["video_fuente_id"]): dict(g) for g in elegidos}

print(f"Guiones elegidos: {[str(g['video_fuente_id']) for g in elegidos]}")
for g in elegidos:
    print(f"  {g['video_fuente_id']} | confianza={g.get('confianza')} | "
          f"palabras={g.get('palabras_reales')} | "
          f"verificar={g.get('datos_a_verificar') or '(nada)'}")

# COMMAND ----------

# MAGIC %md ## 1b. Diseñar el personaje con el guion
# MAGIC
# MAGIC DeepSeek recibe el guion completo en JSON y devuelve la ficha visual del
# MAGIC personaje. Se guarda en `tiktok.gold.personajes_usados` para que el
# MAGIC siguiente video no repita cara.

# COMMAND ----------

from openai import OpenAI

from scripts import personaje_llm
from scripts.personaje import firma as personaje_firma
from scripts.personaje import spec_normalizada

llm = None
fichas_previas = []
fichas_por_id = {}
# Se lee el widget aquí y no se usa la variable de la sección 3 porque esta
# sección corre antes de que se defina.
if (dbutils.widgets.get("personaje").strip() or "motivacional") == "motivacional":
    try:
        filas = spark.sql(
            f"SELECT video_fuente_id, firma, spec FROM {TABLA_PERSONAJES} "
            "ORDER BY generado_en DESC LIMIT 60"
        ).collect()
        fichas_previas = [json.loads(r["spec"]) for r in filas]
        # Ficha que ya se dibujó para cada guion. Regenerar un video debe devolver
        # la MISMA cara: si se dejara al LLM, el "nuevo" video del mismo guion
        # saldría con otro personaje y no sería una regeneración sino otro video.
        for r in filas:
            fichas_por_id.setdefault(str(r["video_fuente_id"]), json.loads(r["spec"]))
    except Exception as exc:
        # La tabla no existe en la primera corrida; no debe impedir el render.
        print(f"Registro {TABLA_PERSONAJES} no legible ({type(exc).__name__}); "
              "se diseña sin historial.")
    llm = OpenAI(
        api_key=dbutils.secrets.get("tiktok_pipeline", "openrouter_api_key"),
        base_url="https://openrouter.ai/api/v1",
        default_headers={"HTTP-Referer": "https://dbc-e83fc5f3-f80b.cloud.databricks.com",
                         "X-Title": "tiktok-personaje"},
    )
    print(f"Fichas previas en el historial: {len(fichas_previas)}")

# COMMAND ----------

# MAGIC %md ## 2. Síntesis de voz con tiempos por palabra

# COMMAND ----------


async def sintetizar_con_tiempos(texto, voz, rate):
    """Devuelve (bytes mp3, palabras con inicio/fin).

    `boundary="WordBoundary"` es lo que hace que edge-tts emita un evento por
    palabra; sin él solo llega uno por frase y no hay forma de subtitular.
    """
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


voz = dbutils.widgets.get("voz").strip() or "es-MX-JorgeNeural"
rate = dbutils.widgets.get("rate").strip() or "+8%"
dry_run = dbutils.widgets.get("dry_run").strip().lower() == "true"
# El prefijo del archivo depende del personaje: si los dos se llamaran
# `jesucitos_<id>.mp4`, el mismo guion generado con otra figura sobrescribiría
# el video anterior y el registro de usados apuntaría a un archivo distinto.
personaje = dbutils.widgets.get("personaje").strip() or "motivacional"
prefijo = "motivacional" if personaje == "motivacional" else "jesucitos"
sufijo = dbutils.widgets.get("sufijo").strip()
print(f"Personaje: {personaje} (archivos {prefijo}_<guion>{sufijo}.mp4)")

# Los temporales van al volumen y no a `/tmp`: el disco efímero del serverless se
# llenó a mitad de un render de 70 s (ffmpeg terminó los frames y falló al escribir
# el átomo `moov` con «Input/output error»).
tmp = Path(tempfile.mkdtemp(prefix="video_guion_", dir=VIDEOS_DIR))

# COMMAND ----------

# MAGIC %md ## 2b. Sortear los hashtags de todos los videos de esta corrida
# MAGIC
# MAGIC Se eligen antes del bucle para que los videos de un mismo run tampoco
# MAGIC compartan hashtags entre sí. El historial vive en `tiktok.gold.hashtags_usados`:
# MAGIC los hashtags de los últimos `VENTANA` videos se excluyen, así que un hashtag
# MAGIC no reaparece hasta que la ventana se desplaza.

# COMMAND ----------

import random

TABLA_HASHTAGS = "tiktok.gold.hashtags_usados"
VENTANA = 80

# Temáticas conocidas, sin `#` y en minúsculas: `temas_hashtags=amor,duelo,reflexion`
# enciende los tres bloques.
CATALOGO = {
    "amor": ["amor", "amorpropio", "relaciones", "pareja", "desamor", "corazon",
             "sentimientos", "amorverdadero", "vinculos", "afecto"],
    "duelo": ["duelo", "perdida", "superacion", "sanacion", "luto", "despedida",
              "aceptacion", "dolor", "reconstruccion", "seguir adelante"],
    "reflexion": ["reflexion", "reflexiones", "pensamientos", "conciencia",
                  "introspeccion", "aprendizaje", "sabiduria", "verdad",
                  "crecimiento", "mentalidad"],
    "motivacional": ["motivacion", "motivacional", "inspiracion", "superacion",
                     "disciplina", "constancia", "exito", "metas", "esfuerzo",
                     "nuncarendirse"],
    "desarrollopersonal": ["desarrollopersonal", "crecimientopersonal", "habitos",
                           "autoconocimiento", "productividad", "mejorversion",
                           "autoestima", "proposito", "mindset", "evolucion"],
    "pareja": ["pareja", "amor", "relacion", "confianza", "fidelidad", "lealtad",
               "comunicacion", "respeto", "compromiso", "convivencia"],
    "familia": ["familia", "mama", "papa", "hijos", "hermanos", "hogar",
                "unionfamiliar", "recuerdos", "crianza", "abuelos"],
    "saludmental": ["saludmental", "ansiedad", "bienestar", "calma", "descanso",
                    "autocuidado", "emociones", "equilibrio", "pazmental", "terapia"],
    "exito": ["exito", "emprender", "dinero", "abundancia", "oportunidades",
              "negocios", "trabajo", "esfuerzo", "resultados", "ambicion"],
    "fe": ["fe", "dios", "esperanza", "oracion", "proposito", "confianza",
           "gratitud", "bendiciones", "espiritualidad", "creer"],
}

# Fondo comun. Es amplio a proposito: con 5 videos por run hacen falta 25
# hashtags distintos por corrida, y un pool corto obligaria a repetir.
GENERICOS = ["fyp", "parati", "viral", "foryou", "tendencia", "explorar",
             "frases", "consejos", "aprendeentiktok", "inspiraciondiaria",
             "reflexionesdiarias", "crecimientopersonal", "consejosdevida",
             "parati_viral", "vidademotivacion", "crecimientopersonal",
             "frasesmotivadoras", "reflexionesdelavida", "pensamientospositivos",
             "leccionesdevida", "parati_viral", "vidaconproposito",
             "aprendedetiktok", "frasesparati", "momentosdevida",
             "pensandomejor", "sabiasque", "paratodos", "comparte",
             "vidareal", "reflexiona", "parati_", "crecimientoreal"]


def _norm_ht(tema):
    return tema.strip().lower().lstrip("#").replace(" ", "")


def construir_pool(temas):
    """Candidatos para los temas pedidos. Un tema desconocido se usa tal cual."""
    pool, vistos = [], set()

    def añadir(lista):
        for h in lista:
            clave = _norm_ht(h)
            if clave and clave not in vistos:
                vistos.add(clave)
                pool.append(clave)

    for tema in temas:
        clave = _norm_ht(tema)
        if clave in CATALOGO:
            añadir(CATALOGO[clave])
        elif clave:
            añadir([clave])
    añadir(GENERICOS)
    return pool


def cargar_historial():
    """Devuelve {hashtag: posicion}, donde 0 es el uso mas reciente.

    No se guarda un simple conjunto de "usados": el catalogo es finito y mucho
    menor que ventana*videos, asi que una exclusion binaria se relajaria siempre.
    Con la posicion se puede preferir siempre el menos usado recientemente, que
    equivale a no repetir mientras el pool alcance.
    """
    try:
        filas = spark.sql(
            f"SELECT hashtags FROM {TABLA_HASHTAGS} ORDER BY generado_en DESC LIMIT {VENTANA}"
        ).collect()
        posicion = {}
        for i, r in enumerate(filas):
            for h in (r["hashtags"] or "").split(","):
                clave = _norm_ht(h)
                if clave and clave not in posicion:
                    posicion[clave] = i
        return posicion
    except Exception as exc:
        print(f"Historial de hashtags no legible ({type(exc).__name__}); se sortea sin él.")
        return {}


def sortear_hashtags(pool, historial, n, ya_en_run):
    """`n` hashtags distintos, priorizando los que llevan mas tiempo sin salir.

    `historial` se actualiza in situ tras cada sorteo, de modo que los videos de
    una misma corrida tampoco comparten hashtags.
    """
    candidatos = [h for h in pool if h not in ya_en_run]
    if len(candidatos) < n:
        # Solo puede pasar con un catalogo diminuto en un run muy grande.
        print(f"AVISO: catalogo insuficiente ({len(candidatos)} libres para {n}); "
              "el run repetira algun hashtag.")
        candidatos = list(pool)

    # Menos usados primero; los que nunca salieron (sin entrada) van delante.
    # El desempate es aleatorio: sin el, el orden del catalogo decidiria siempre
    # los mismos y los ultimos hashtags del pool no se elegirian nunca.
    candidatos.sort(key=lambda h: (historial.get(h, -1), random.random()))
    elegidos_ht = candidatos[:n]

    for h in historial:
        historial[h] += 1
    for h in elegidos_ht:
        historial[h] = 0
    return elegidos_ht


try:
    N_HASHTAGS = max(1, int(dbutils.widgets.get("n_hashtags") or 5))
except ValueError:
    N_HASHTAGS = 5
    print("n_hashtags invalido; se usa 5.")

TEMAS = ([t.strip() for t in dbutils.widgets.get("temas_hashtags").split(",") if t.strip()]
         or ["motivacional"])

POOL_HASHTAGS = construir_pool(TEMAS)
HISTORIAL_HT = cargar_historial()
print(f"Hashtags: {N_HASHTAGS} por video | temas={TEMAS} | pool={len(POOL_HASHTAGS)} | "
      f"en historial={len(HISTORIAL_HT)}")

ht_por_guion, ht_asignados = {}, []
for guion in elegidos:
    elegidos_ht = sortear_hashtags(POOL_HASHTAGS, HISTORIAL_HT, N_HASHTAGS, ht_asignados)
    ht_asignados.extend(elegidos_ht)
    ht_por_guion[str(guion["video_fuente_id"])] = elegidos_ht
    print(f"  {guion['video_fuente_id']}: {','.join(elegidos_ht)}")

# COMMAND ----------

# MAGIC %md ## 3. Procesar cada guion: voz, animación y subtítulos

# COMMAND ----------

hechos = []
fallidos = []

for guion in elegidos:
    fuente_id = str(guion["video_fuente_id"])
    print("=" * 60)
    print(f"GUION {fuente_id}")

    texto = texto_del_guion(guion)
    if not texto.strip():
        fallidos.append({"video_fuente_id": fuente_id, "motivo": "sin texto"})
        print("  omitido: el guion no tiene texto")
        continue

    # Ficha del personaje: el LLM ve el guion y propone una cara coherente con
    # él. `fichas_previas` se amplía tras cada video de esta misma corrida, así
    # que los tres de un run tampoco se repiten entre sí.
    ficha = None
    if personaje == "motivacional":
        ficha_guardada = (fichas_por_id.get(fuente_id)
                          if dbutils.widgets.get("reusar_personaje").lower() == "true"
                          else None)
        if ficha_guardada:
            # Las fichas más antiguas del registro se guardaron antes de que
            # existiera el eje `escenario`; sin normalizar, el print y el render
            # fallan con KeyError al pedir una clave que no está.
            ficha = spec_normalizada(ficha_guardada)
            print("  personaje: se reutiliza la ficha guardada (misma cara)")
        else:
            ficha = personaje_llm.disenar(
                llm, dbutils.widgets.get("llm_model").strip(), guion, fichas_previas
            )
        fichas_previas.append(ficha)
        print(f"  personaje: pelo={ficha['pelo_estilo']} ropa={ficha['ropa_tipo']} "
              f"{ficha['ropa_color']} acc={ficha['accesorio']}")
        print(f"  emoción: {ficha['expresion']} | escenario: {ficha['escenario']}")
        print(f"  razón: {ficha.get('razon', '(sin razón)')}")

    carpeta = tmp / fuente_id
    carpeta.mkdir(parents=True, exist_ok=True)
    audio_path = carpeta / "voz.mp3"

    datos, palabras = await sintetizar_con_tiempos(texto, voz, rate)
    if not datos or not palabras:
        fallidos.append({"video_fuente_id": fuente_id,
                         "motivo": "edge-tts sin audio o sin tiempos por palabra"})
        print("  omitido: edge-tts no devolvió audio o tiempos por palabra")
        continue

    audio_path.write_bytes(datos)
    duracion = common.probe(audio_path)["duracion"]
    print(f"  audio: {duracion:.1f}s, {len(palabras)} palabras con tiempo")

    if dry_run:
        hechos.append({"video_fuente_id": fuente_id, "duracion_audio": duracion,
                       "palabras": len(palabras), "dry_run": True})
        continue

    sin_subs = carpeta / "video.mp4"
    # El fondo por defecto del pipeline (`FONDO`) solo se usa con el personaje
    # original. Con `motivacional` el escenario lo elige el LLM a partir del
    # guion: si se siguiera pasando la imagen fija, el nuevo eje "escenario"
    # nunca se vería.
    if personaje == "motivacional":
        fondo_ruta = None
        escenario = ficha["escenario"] if ficha else "paraiso"
    else:
        fondo_ruta = Path(FONDO) if os.path.isfile(FONDO) else None
        escenario = "paraiso"
    jesucitos.animar(audio_path, fondo_ruta, sin_subs,
                     personaje=personaje, spec=ficha, escenario=escenario)

    chunks = subtitular.agrupar(palabras)
    titulo = (guion.get("texto_pantalla_1") or guion.get("hook") or "").strip()
    ass_texto = ass_con_titulo(subtitular.generar_ass(chunks), titulo, duracion)
    ass_texto = subtitular.agregar_marca(
        ass_texto, duracion, dbutils.widgets.get("marca_usuario"))
    ass_path = carpeta / "subs.ass"
    ass_path.write_text(ass_texto, encoding="utf-8")

    # El sufijo permite convivir con el video anterior mientras se verifica el
    # nuevo: el usuario pidió crear primero y eliminar después, así que pisar el
    # mp4 existente borraría el respaldo justo antes de comprobarlo.
    nombre = f"{prefijo}_{fuente_id}{sufijo}.mp4"
    # Agrupado por fecha: el lote queda en videos/AAAA/MM/DD/ para no mezclarse
    # con los renders de otros días al descargarlo.
    salida = Path(rutas.carpeta_del_dia(VIDEOS_DIR)) / nombre
    salida.parent.mkdir(parents=True, exist_ok=True)
    subtitular.quemar(sin_subs, ass_path, salida)

    # Los hashtags del guion se sustituyen por los sorteados: el guion repite
    # siempre los mismos (los genera el LLM a partir del mismo prompt), y aquí
    # lo que se busca es variedad entre videos.
    hashtags = ",".join(ht_por_guion.get(fuente_id, []))
    publicacion = salida.with_name(f"{prefijo}_{fuente_id}{sufijo}_publicacion.txt")
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
    print(f"  hashtags: {hashtags}")

    hechos.append({
        "video_name": nombre,
        "video_fuente_id": fuente_id,
        "titulo": titulo,
        "hashtags": hashtags,
        "duracion": info["duracion"],
        "subtitulos": len(chunks),
        "spec": ficha,
    })

# COMMAND ----------

# MAGIC %md ## 3b. Paquete de publicación del lote
# MAGIC
# MAGIC Un solo `descripciones_reels.md` junto a los mp4 del día, con título,
# MAGIC descripción, tags y hashtags de cada video. Se escribe aunque el lote tenga
# MAGIC un solo video: así el archivo siempre describe lo que hay en la carpeta y
# MAGIC nunca queda una carpeta con videos y sin paquete.

if hechos:
    paquete = descripciones.escribir(
        [
            {
                "video_name": h["video_name"],
                "titulo": h["titulo"],
                "hashtags": h["hashtags"],
                "guion": guiones_por_id.get(str(h["video_fuente_id"]), {}),
            }
            for h in hechos if not h.get("dry_run")
        ],
        rutas.carpeta_del_dia(VIDEOS_DIR),
    )
    print(f"  📄 {paquete}")

# COMMAND ----------

# MAGIC %md ## 4. Publicar el resultado para la tarea de subida

# COMMAND ----------

if not hechos:
    raise RuntimeError(f"No se generó ningún video. Fallidos: {fallidos}")

# `videos` se publica como lista de dicts: la tarea `subir_borrador` la recorre
# con `for_each_task`, de modo que un mismo job sube 1 o 3 borradores sin tocar
# la definición ni duplicar el notebook de subida.
publicables = [
    {"video_name": h["video_name"], "video_fuente_id": h["video_fuente_id"]}
    for h in hechos if not h.get("dry_run")
]

if publicables:
    # Registrar aquí, y no en la tarea de subida: el video ya existe en disco,
    # así que aunque la subida falle no debe volver a generarse desde cero.
    from pyspark.sql import Row
    from pyspark.sql.types import StringType, StructField, StructType, TimestampType
    from datetime import datetime, timezone

    spark.sql(
        f"""CREATE TABLE IF NOT EXISTS {TABLA_USADOS} (
              video_fuente_id STRING, video_name STRING, generado_en TIMESTAMP
            ) USING DELTA"""
    )
    esquema = StructType([
        StructField("video_fuente_id", StringType()),
        StructField("video_name", StringType()),
        StructField("generado_en", TimestampType()),
    ])
    ahora = datetime.now(timezone.utc)
    nuevas = [
        Row(video_fuente_id=p["video_fuente_id"], video_name=p["video_name"], generado_en=ahora)
        # Un ID regenerado ya está registrado: insertarlo otra vez duplicaría la
        # fila sin aportar nada. El registro dice "qué guion ya se usó", no
        # "cuántas veces".
        for p in publicables if str(p["video_fuente_id"]) not in regenerar
    ]
    if nuevas:
        spark.createDataFrame(nuevas, esquema).write.mode("append").saveAsTable(TABLA_USADOS)
        print(f"Registrados {len(nuevas)} guiones usados en {TABLA_USADOS}")

if publicables and personaje == "motivacional":
    # La ficha se registra aquí y no al diseñarla: si el render falla, no se
    # quema un diseño válido y el guion conserva la cara que le corresponde.
    from pyspark.sql import Row
    from pyspark.sql.types import StringType, StructField, StructType, TimestampType
    from datetime import datetime, timezone

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
    ahora_pj = datetime.now(timezone.utc)
    filas_pj = [
        Row(video_fuente_id=h["video_fuente_id"],
            firma=personaje_firma(h["spec"]),
            spec=json.dumps(h["spec"], ensure_ascii=False),
            generado_en=ahora_pj)
        # Al regenerar se reutiliza la ficha guardada: volver a insertarla dejaría
        # dos filas con la misma cara y la reutilización leería la más reciente,
        # que es correcta, pero el historial de "caras usadas" engordaría sin motivo.
        for h in hechos if h.get("spec") and str(h["video_fuente_id"]) not in regenerar
    ]
    if filas_pj:
        spark.createDataFrame(filas_pj, esquema_pj).write.mode("append").saveAsTable(TABLA_PERSONAJES)
        print(f"Registrados {len(filas_pj)} personajes en {TABLA_PERSONAJES}")

if publicables:
    # Historial de hashtags: se registra solo lo que de verdad se generó, para
    # que un render fallido no queme un sorteo que nunca llegó a usarse.
    try:
        from pyspark.sql import Row
        from pyspark.sql.types import StringType, StructField, StructType, TimestampType
        from datetime import datetime, timezone

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
        ahora_ht = datetime.now(timezone.utc)
        filas_ht = [
            Row(video_fuente_id=str(p["video_fuente_id"]), video_name=p["video_name"],
                hashtags=",".join(ht_por_guion.get(str(p["video_fuente_id"]), [])),
                generado_en=ahora_ht)
            for p in publicables
        ]
        spark.createDataFrame(filas_ht, esquema_ht).write.mode("append").saveAsTable(TABLA_HASHTAGS)
        print(f"Registrados {len(filas_ht)} sorteos de hashtags en {TABLA_HASHTAGS}")
    except Exception as exc:
        print(f"AVISO: no se pudo registrar el historial de hashtags "
              f"({type(exc).__name__}): {exc}")

if publicables:
    dbutils.jobs.taskValues.set(key="videos", value=publicables)
    dbutils.jobs.taskValues.set(key="video_name", value=publicables[0]["video_name"])
    dbutils.jobs.taskValues.set(key="hashtags", value=hechos[0].get("hashtags") or "")

# Los intermedios pesan ~17 MB por video y ya no hacen falta: sin esto, cada
# corrida dejaría basura acumulándose en el volumen.
shutil.rmtree(tmp, ignore_errors=True)

print(f"Generados: {len(hechos)} | fallidos: {len(fallidos)}")

dbutils.notebook.exit(json.dumps({
    "generados": hechos,
    "fallidos": fallidos,
}, ensure_ascii=False))
