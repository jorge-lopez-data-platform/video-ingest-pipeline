# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# dependencies = [
#   "anthropic",
# ]
# ///
# MAGIC %md
# MAGIC # TikTok → Guiones 60s alta retención (Claude Haiku 5.5)
# MAGIC Scrape (Apify) → filtros (español, ≤30d, multivirales) → LLM (Claude vía Anthropic SDK) → Delta.
# MAGIC Tokens viven en el secret scope `tiktok_pipeline` (`apify_token`, `anthropic_api_key`). Nunca se hardcodean.

# COMMAND ----------

# MAGIC %pip install anthropic

# COMMAND ----------

import json
import math
import random
import re
import time
from datetime import datetime

import requests
from pyspark.sql import Row
from pyspark.sql.types import (DoubleType, LongType, StringType,
                               StructField, StructType, TimestampType)

# ============================================================
# ⚙️ PARÁMETROS (sobrescribibles desde el job)
# ============================================================
dbutils.widgets.text("hashtags", "superacionpersonal,emociones,sentimientos,depresion,ganasdevivir,saludmental,tristeza,vivir,autoestima,ansiedad,resiliencia,soledad,reflexion,motivacion")  # pool; cada corrida toma `hashtags_por_corrida` al azar
dbutils.widgets.text("results_per_hashtag", "25")
dbutils.widgets.text("max_charge_usd", "3.0")
dbutils.widgets.text("min_plays", "1000000")
dbutils.widgets.text("min_likes", "15000")
dbutils.widgets.text("post_urls", "")  # si se indica, scrapea solo estas URLs (coma-separadas) en vez de hashtags
dbutils.widgets.text("max_duration_sec", "60")
dbutils.widgets.text("max_age_days", "14")
dbutils.widgets.text("top_n", "12")
dbutils.widgets.text("guiones_objetivo", "5")  # se deja de generar al alcanzarlo
dbutils.widgets.text("max_llamadas_llm", "12")  # tope duro de llamadas a Claude por corrida
dbutils.widgets.text("hashtags_por_corrida", "5")  # se rota del pool de `hashtags`
dbutils.widgets.text("script_seconds_target", "60")
dbutils.widgets.text("max_tokens_por_peticion", "8000")
dbutils.widgets.text("llm_model", "claude-haiku-5-5")
dbutils.widgets.text("descartar_con_verificar", "true")  # descarta guiones con confianza "baja"
dbutils.widgets.text("run_timeout_secs", "900")
dbutils.widgets.dropdown("usar_apify", "true", ["true", "false"])  # false = no scrapea; usa los guiones ya guardados

HASHTAGS_POOL = [h.strip() for h in dbutils.widgets.get("hashtags").split(",") if h.strip()]
HASHTAGS = random.sample(HASHTAGS_POOL, min(int(dbutils.widgets.get("hashtags_por_corrida")), len(HASHTAGS_POOL)))
GUIONES_OBJETIVO = int(dbutils.widgets.get("guiones_objetivo"))
MAX_LLAMADAS_LLM = int(dbutils.widgets.get("max_llamadas_llm"))
RESULTS_PER_HASHTAG = int(dbutils.widgets.get("results_per_hashtag"))
MAX_CHARGE_USD = float(dbutils.widgets.get("max_charge_usd"))
MIN_PLAYS = int(dbutils.widgets.get("min_plays"))
MIN_LIKES = int(dbutils.widgets.get("min_likes"))
POST_URLS = [u.strip() for u in dbutils.widgets.get("post_urls").split(",") if u.strip()]
MAX_DURATION = int(dbutils.widgets.get("max_duration_sec"))
MAX_AGE_DAYS = int(dbutils.widgets.get("max_age_days"))
TOP_N = int(dbutils.widgets.get("top_n"))
TARGET_SECS = int(dbutils.widgets.get("script_seconds_target"))
LLM_MODEL = dbutils.widgets.get("llm_model")
DESCARTAR_CON_VERIFICAR = dbutils.widgets.get("descartar_con_verificar").strip().lower() == "true"
RUN_TIMEOUT_SECS = int(dbutils.widgets.get("run_timeout_secs"))
USAR_APIFY = dbutils.widgets.get("usar_apify").strip().lower() != "false"
TABLA_GUIONES = "tiktok.gold.guiones_60s"

# Sin Apify el job no scrapea ni regenera guiones: sale con éxito para que
# `generar_video` consuma lo que ya haya en `tiktok.gold.guiones_60s`. Salir
# antes de la FASE 1 evita tambien el `overwrite` de la FASE 5, que borraria
# los guiones existentes sin reponerlos.
if not USAR_APIFY:
    print("usar_apify=false: se omiten el scrapeo y la regeneracion de guiones.")
    print(f"Se reutiliza lo que ya exista en {TABLA_GUIONES}.")
    dbutils.notebook.exit(json.dumps({"source": "apify_skipped", "tabla": TABLA_GUIONES}))

ACTOR = "clockworks~tiktok-scraper"  # actor ya integrado en este workspace; esquema conocido
API = "https://api.apify.com/v2"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
WORDS_PER_SEC = 2.7
TARGET_WORDS = int(TARGET_SECS * WORDS_PER_SEC)  # ~162 para 60s
MAX_WORDS = int(TARGET_WORDS * 1.03)  # tope duro verificado por código (~166)

# ============================================================
# 🔑 SECRETOS
# ============================================================
apify_token = dbutils.secrets.get("tiktok_pipeline", "apify_token")
anthropic_key = dbutils.secrets.get("tiktok_pipeline", "anthropic_api_key")

session = requests.Session()
session.headers.update({"Authorization": f"Bearer {apify_token}"})

# ============================================================
# 🔧 HELPERS (defensivos: toleran clockworks~tiktok-scraper y apify/tiktok-data-scraper)
# ============================================================
def _g(v, *names, default=None):
    for n in names:
        if isinstance(v, dict) and v.get(n) is not None:
            return v[n]
    return default

def get_plays(v):
    return int(_g(v, "playCount", "play_count", default=0) or 0)

def get_likes(v):
    return int(_g(v, "diggCount", "digg_count", default=0) or 0)

def get_duration(v):
    meta = v.get("videoMeta")
    raw = _g(meta if isinstance(meta, dict) else {}, "duration", default=None)
    if raw is None:
        raw = v.get("duration")
    if raw is None:
        return 9999
    raw = int(raw)
    return raw // 1000 if raw > 1000 else raw  # si viene en ms, convertir a s

def get_desc(v):
    return _g(v, "text", "description", "desc", "caption", default="") or ""

def get_created_iso(v):
    return _g(v, "createTimeISO", "create_time", default=None)

def get_verified(v):
    meta = v.get("authorMeta")
    if isinstance(meta, dict):
        return bool(meta.get("verified", False))
    return bool(_g(v, "author_verified", "verified", default=False))

def es_espanol(v):
    # Primero el campo nativo del scraper (rápido y fiable); fallback a heurística.
    lang = _g(v, "textLanguage", "text_language", default=None)
    if isinstance(lang, str) and lang:
        return lang.lower().startswith("es")
    desc = get_desc(v)
    if not desc or len(desc.strip()) < 10:
        return False
    return bool(re.search(r"[áéíóúüñ¿¡]", desc, re.IGNORECASE))

# ============================================================
# 🕷️ FASE 1: SCRAPEO APIFY
# ============================================================
print("=" * 60)
print("🕷️  FASE 1: Scrapeo Apify — videos trending en español")
print("=" * 60)

run_input = ({"postURLs": POST_URLS} if POST_URLS
             else {"hashtags": HASHTAGS, "resultsPerPage": RESULTS_PER_HASHTAG})
print("Modo:", "URLs específicas" if POST_URLS else f"hashtags {HASHTAGS}")

resp = session.post(
    f"{API}/acts/{ACTOR}/runs",
    params={"maxTotalChargeUsd": MAX_CHARGE_USD, "timeout": RUN_TIMEOUT_SECS},
    json=run_input,
    timeout=60,
)
resp.raise_for_status()
run = resp.json()["data"]
run_id, dataset_id = run["id"], run["defaultDatasetId"]
print(f"Started Apify run {run_id}, dataset {dataset_id}")

deadline = time.time() + RUN_TIMEOUT_SECS + 120
while True:
    r = session.get(f"{API}/actor-runs/{run_id}", timeout=60)
    r.raise_for_status()
    run = r.json()["data"]
    if run["status"] in TERMINAL:
        break
    if time.time() > deadline:
        session.post(f"{API}/actor-runs/{run_id}/abort", timeout=60)
        raise TimeoutError(f"Apify run {run_id} did not finish in time; abort requested")
    time.sleep(10)

print(f"Run finished: {run['status']} | {run.get('statusMessage')} | usage USD {run.get('usageTotalUsd')}")
if run["status"] != "SUCCEEDED":
    raise RuntimeError(f"Apify run {run_id} ended with status {run['status']}: {run.get('statusMessage')}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## FASE 2: descarga del dataset

# COMMAND ----------

items, offset, page = [], 0, 500
while True:
    r = session.get(
        f"{API}/datasets/{dataset_id}/items",
        params={"format": "json", "clean": "true", "limit": page, "offset": offset},
        timeout=120,
    )
    r.raise_for_status()
    batch = r.json()
    if not batch:
        break
    items.extend(batch)
    offset += len(batch)

print(f"📥 Scrapeados: {len(items)}")
if not items:
    raise RuntimeError(f"Apify dataset {dataset_id} is empty; refusing to continue silently")

# ============================================================
# 🔍 FASE 3: FILTROS — único | español | +min_plays | ≤max_duration | ≤max_age | sin ✔️
# ============================================================
print("\n" + "=" * 60)
print("🔍 FASE 3: Filtros")
print("=" * 60)

now = datetime.utcnow()

def parse_iso(s):
    if not s:
        return None
    s = s.replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None

# Filtro 1: deduplicar y descartar videos que ya tienen guion (no pagar Claude dos veces)
ya_procesados = set()
for tabla_registro in (TABLA_GUIONES, "tiktok.gold.guiones_usados"):
    try:
        ya_procesados |= {str(r["video_fuente_id"])
                          for r in spark.sql(f"SELECT video_fuente_id FROM {tabla_registro}").collect()}
    except Exception as exc:
        print(f"Registro {tabla_registro} no legible ({type(exc).__name__}); se asume vacío.")
seen = set()
unique = []
for v in items:
    vid = v.get("id") or v.get("video_id")
    if vid and vid not in seen and str(vid) not in ya_procesados:
        seen.add(vid)
        unique.append(v)
print(f"♻️ Excluidos por ya procesados: {len(items) - len(unique)}")

def filtrar(min_plays, min_likes, solo_espanol=True, sin_verificado=True):
    """Aplica los filtros de contenido con umbrales explícitos.

    Duración y antigüedad NO dependen de los umbrales: son requisito del
    producto (video de 60 s y fuente fresca), así que nunca se relajan.
    """
    out = []
    for v in unique:
        if solo_espanol and not es_espanol(v):
            continue
        if get_plays(v) < min_plays or get_likes(v) < min_likes:
            continue
        if get_duration(v) > MAX_DURATION:
            continue
        dt = parse_iso(get_created_iso(v))
        if dt is not None and (now - dt).days > MAX_AGE_DAYS:
            continue
        if sin_verificado and get_verified(v):
            continue
        out.append(v)
    return out

# Los umbrales de un tema masivo dejan la lista vacía en temas de nicho
# (amor, duelo, reflexión): un run sin guiones deja al job sin nada que
# producir. Se reintenta bajando el listón antes de rendirse.
# Las vistas mínimas son un piso fijo: sólo se relajan los likes y el idioma.
ESCALONES = [
    (MIN_PLAYS, MIN_LIKES, True, True),
    (MIN_PLAYS, max(1000, MIN_LIKES // 5), True, True),
    (MIN_PLAYS, max(1000, MIN_LIKES // 5), False, True),
]
# Un solo candidato no basta: el LLM puede rechazarlo por longitud o por
# llevar datos sin verificar, y entonces la corrida acaba con cero guiones.
# Se busca un mínimo, y si ningún escalón lo alcanza se queda el que más dio.
MIN_CANDIDATOS = 6
candidatos, escalon_usado = [], None
for i, (mp, ml, esp, sv) in enumerate(ESCALONES):
    prueba = filtrar(mp, ml, esp, sv)
    print(f"   filtro {i}: min_plays={mp:,} min_likes={ml:,} "
          f"español={esp} sin_verificado={sv} -> {len(prueba)}")
    if len(prueba) > len(candidatos):
        candidatos, escalon_usado = prueba, i
    if len(prueba) >= MIN_CANDIDATOS:
        break
if escalon_usado not in (None, 0):
    print(f"AVISO: se relajaron los filtros (escalón {escalon_usado}) para "
          "esta temática; el pool de videos es pequeño.")

# Ranking: popularidad acumulada + velocidad (vistas por hora desde la publicación).
# Un video viral de hace 25 días ya no tiene momentum; uno de 3 días con crecimiento sí.
def velocidad_por_hora(v):
    dt = parse_iso(get_created_iso(v))
    if dt is None:
        return 0.0
    horas = max((now - dt).total_seconds() / 3600, 1.0)
    return get_plays(v) / horas

def viral_score(v):
    plays = get_plays(v)
    shares = int(_g(v, "shareCount", "share_count", default=0) or 0)
    comments = int(_g(v, "commentCount", "comment_count", default=0) or 0)
    likes = int(_g(v, "diggCount", "digg_count", default=0) or 0)
    eng = (likes + shares + comments) / max(plays, 1)
    return (0.20 * math.log10(plays + 1)
            + 0.20 * math.log10(shares + 1)
            + 0.10 * math.log10(comments + 1)
            + 0.10 * math.log10(likes + 1)
            + 0.10 * (eng * 100)
            + 0.30 * math.log10(velocidad_por_hora(v) + 1))

candidatos.sort(key=viral_score, reverse=True)
candidatos = candidatos[:TOP_N]

print(f"📥 Raw:               {len(items)}")
print(f"✅ Únicos:            {len(unique)}")
print(f"🏆 CANDIDATOS FINALES: {len(candidatos)} (top {TOP_N})")

if not candidatos:
    print("❌ Cero candidatos ni con los filtros relajados: amplía los hashtags "
          "o sube results_per_hashtag.")
    dbutils.notebook.exit(json.dumps({"scrapeados": len(items), "candidatos": 0, "guiones": 0}))

# COMMAND ----------
# MAGIC %md
# MAGIC ## FASE 4: guiones 60s con Claude Haiku 5.5 (Anthropic SDK)

# COMMAND ----------

import anthropic

llm = anthropic.Anthropic(api_key=anthropic_key)

GUION_ESQUEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "open_loop": {"type": "string"},
        "bloque_1": {"type": "string"},
        "pattern_interrupt": {"type": "string"},
        "bloque_2": {"type": "string"},
        "pre_cierre": {"type": "string"},
        "cierre": {"type": "string"},
        "cta": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "texto_en_pantalla": {"type": "array", "items": {"type": "string"}},
        "datos_a_verificar": {"type": "array", "items": {"type": "string"}},
        "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
    },
    "required": ["hook", "open_loop", "bloque_1", "pattern_interrupt", "bloque_2", "pre_cierre",
                 "cierre", "cta", "hashtags", "texto_en_pantalla", "datos_a_verificar", "confianza"],
    "additionalProperties": False,
}

PROMPT_SYSTEM = """Eres el mejor guionista de TikTok en español. Has escrito guiones para cuentas de +10M followers. Tu obsesión: RETENCIÓN > 80% hasta el último segundo.

REGLAS SAGRADAS:
- Cada guion dura EXACTAMENTE {secs} segundos al leer en voz alta (~{words} palabras a 2.7 pal/seg).
- Cuenta tus palabras ANTES de entregar. {lo}-{hi} palabras = correcto. Fuera = reescribe.
- NO copies el original. Mismo TEMA, pero redactado con tus propias palabras.

REGLAS DE VERACIDAD (prioridad MÁXIMA, por encima del impacto):
- Usa SOLO datos que conozcas con certeza y que sean ampliamente documentados. Ante la duda, NO lo incluyas.
- PROHIBIDO inventar: cifras exactas, porcentajes, estudios, universidades, nombres de investigadores, fechas, BPM, ventas, lugares o citas. Si no recuerdas la cifra exacta, usa lenguaje cualitativo ("millones", "una de las más…") o cambia de dato.
- PROHIBIDO presentar mitos, rumores o leyendas urbanas como hechos. Si algo es una leyenda, dilo ("según la leyenda…") o descártalo.
- PROHIBIDO afirmar hechos negativos o delicados sobre personas reales, instituciones o países sin evidencia sólida.
- No mezcles lugares, personas o eventos distintos como si fueran uno solo (verifica que el nombre y el dato correspondan).
- El OPEN LOOP es una promesa: debe cumplirse en el PRE-CIERRE con un dato real. Si no tienes un dato real que cumpla la promesa, escribe un open loop más modesto que sí puedas cumplir.
- Prefiere 2 datos sólidos y verificables a 4 espectaculares y dudosos. La retención se logra con ritmo y curiosidad, no con exageración.
- Si el tema original no se puede tratar con datos fiables, elige un ángulo cercano que sí puedas sostener.

REGLAS DE COHERENCIA Y CIERRE (obligatorias):
- El OPEN LOOP debe prometer algo CONCRETO (un nombre, un lugar, un hecho identificable), no algo vago como "uno de ellos" o "un secreto". El PRE-CIERRE debe revelar exactamente eso, con nombre propio. Antes de entregar, comprueba que lo prometido aparece literalmente en el pre-cierre; si no, reescribe el open loop o el pre-cierre.
- NO presentes escenarios hipotéticos ni historias inventadas (p. ej. "una cafetería que se endeudó") como si fueran hechos. Si usas un ejemplo ilustrativo, márcalo explícitamente con "imagina que…" o "por ejemplo".
- Mantente en el TEMA CONCRETO del video original (descripción y hashtags). No lo generalices ni lo cambies por otro tema.
- LONGITUD: nunca superes {hi} palabras en total. Si te pasas, recorta primero el bloque_1 y el bloque_2.

ESTRUCTURA CRONOMETRADA:
SEG 0-3:   HOOK explosivo (1 frase, máx 10 palabras). Dato shock, pregunta imposible.
SEG 3-8:   OPEN LOOP (1-2 frases). Promete algo revelado solo al final.
SEG 8-25:  BLOQUE 1 (datos impactantes, números, imágenes mentales).
SEG 25-28: PATTERN INTERRUPT (giro brusco).
SEG 28-45: BLOQUE 2 (construye al clímax).
SEG 45-50: PRE-CIERRE (revela lo prometido).
SEG 50-57: CIERRE (frase para el recuerdo).
SEG 57-60: CTA NATIVO (orgánico, NUNCA "like y suscríbete").

TITULAR ("texto_en_pantalla"[0]): es el título que se quema en el video y el que se publica. Máx 70 caracteres, sin hashtags, sin comillas ni punto final, y NUNCA marcas de tiempo ("0-5", "overlay", "seg"). No copies el hook literal: si el titular dice exactamente lo mismo que el gancho, reescríbelo. Debe funcionar solo, fuera de contexto.

PROHIBIDO: introducciones, muletillas, relleno. Respondes SIEMPRE en JSON válido, sin markdown.""".format(
    secs=TARGET_SECS, words=TARGET_WORDS, lo=int(TARGET_WORDS * 0.97), hi=int(TARGET_WORDS * 1.03)
)

PROMPT_USUARIO = """Genera un guion de {secs}s basado en este video viral:

📺 VIDEO ORIGINAL:
- Descripción: {desc}
- Plays: {plays}
- Engagement: {eng}%

🎯 Mantente en el TEMA CONCRETO de la descripción (no lo generalices) y escribe un guion propio. Usa únicamente datos que conozcas con certeza y que sean ampliamente documentados; sin cifras, estudios ni nombres inventados. Cumple en el pre-cierre lo que prometas en el open loop.

🔎 AUTOEVALUACIÓN (obligatoria): en "datos_a_verificar" lista cada dato concreto del guion (cifras, fechas, nombres, lugares, afirmaciones) que una persona debería comprobar antes de publicar. En "confianza" indica "alta" (SOLO si "datos_a_verificar" está vacío y todo es de conocimiento común y sólido), "media" (algún dato conviene verificarlo) o "baja" (hay datos de los que no estás seguro).

📋 OUTPUT JSON:
{{
  "hook": "...",
  "open_loop": "...",
  "bloque_1": "...",
  "pattern_interrupt": "...",
  "bloque_2": "...",
  "pre_cierre": "...",
  "cierre": "...",
  "cta": "...",
  "hashtags": ["h1", "h2", "h3", "h4", "h5", "h6"],
  "texto_en_pantalla": ["TITULAR corto del video (el primero se quema arriba y se usa como título; NO escribas marcas de tiempo como '0-5' ni 'overlay')", "REFUERZO: una frase que golpee hacia el segundo 25", "CIERRE: la frase que debe quedarse al final"],
  "datos_a_verificar": ["dato concreto 1", "dato concreto 2"],
  "confianza": "alta | media | baja"
}}"""

def clonar_guion(video):
    desc = get_desc(video)
    plays = get_plays(video)
    likes = int(_g(video, "diggCount", "digg_count", default=0) or 0)
    shares = int(_g(video, "shareCount", "share_count", default=0) or 0)
    comments = int(_g(video, "commentCount", "comment_count", default=0) or 0)
    eng = round((likes + shares + comments) / max(plays, 1) * 100, 2)

    prompt = PROMPT_USUARIO.format(secs=TARGET_SECS, desc=desc if desc else "(sin descripción)", plays=f"{plays:,}", eng=eng)

    resp = llm.messages.create(
        model=LLM_MODEL,
        max_tokens=int(dbutils.widgets.get("max_tokens_por_peticion")),
        system=PROMPT_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": GUION_ESQUEMA}},
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"guion no completado: {resp.stop_reason}")
    guion = json.loads(next(b.text for b in resp.content if b.type == "text"))

    texto = " ".join([guion.get("hook", ""), guion.get("open_loop", ""), guion.get("bloque_1", ""),
                      guion.get("pattern_interrupt", ""), guion.get("bloque_2", ""),
                      guion.get("pre_cierre", ""), guion.get("cierre", ""), guion.get("cta", "")])
    palabras = len(texto.split())
    segundos = round(palabras / WORDS_PER_SEC, 1)
    guion["palabras_reales"] = palabras
    guion["duracion_estimada"] = segundos
    guion["duracion_ok"] = (TARGET_SECS - 5) <= segundos <= (TARGET_SECS + 5)
    guion["video_fuente_id"] = video.get("id") or video.get("video_id")
    guion["video_fuente_url"] = _g(video, "webVideoUrl", "url", default=None)
    guion["video_fuente_plays"] = plays
    guion["video_fuente_desc"] = (desc or "")[:100]
    return guion

guiones_finales = []
rechazados = 0
descartados_verificar = 0
llamadas_llm = 0
for i, video in enumerate(candidatos, 1):
    if len(guiones_finales) >= GUIONES_OBJETIVO or llamadas_llm >= MAX_LLAMADAS_LLM:
        print(f"🛑 Parada: {len(guiones_finales)} guiones, {llamadas_llm} llamadas a Claude")
        break
    print(f"\n🧠 [{i}/{len(candidatos)}] {get_plays(video):,} plays | {get_desc(video)[:50]}...")
    try:
        guion = None
        for intento in range(3):  # 1 intento + hasta 2 reintentos
            if llamadas_llm >= MAX_LLAMADAS_LLM:
                break
            llamadas_llm += 1
            g = clonar_guion(video)
            en_rango = (TARGET_SECS - 6) <= g["duracion_estimada"] <= (TARGET_SECS + 6)
            if g["palabras_reales"] <= MAX_WORDS and en_rango:
                guion = g
                break
            print(f"   🔄 intento {intento + 1}: {g['palabras_reales']} palabras (~{g['duracion_estimada']}s), fuera de rango (máx {MAX_WORDS})")
        if guion is None:
            rechazados += 1
            print("   ⚠️ no cumplió tope de palabras/duración tras 3 intentos, descartado")
        elif (DESCARTAR_CON_VERIFICAR
              and str(guion.get("confianza", "")).strip().lower() == "baja"):
            # El prompt obliga a listar en `datos_a_verificar` cada cifra,
            # nombre o lugar, así que descartar por tener esa lista no vacía
            # mataba el 100% de los guiones. Lo que importa es si el propio
            # modelo declara confianza baja: ahí sí no se publica.
            descartados_verificar += 1
            print(f"   🚫 descartado por confianza baja -> {guion['datos_a_verificar']}")
        else:
            guion["duracion_ok"] = True
            guiones_finales.append(guion)
            print(f"   ✅ {guion['palabras_reales']} palabras (~{guion['duracion_estimada']}s)")
            print(f"   Hook: {guion['hook'][:70]}")
    except Exception as e:
        rechazados += 1
        print(f"   ❌ Error: {e}")

print(f"\n🎬 GUIONES GENERADOS: {len(guiones_finales)} | 🗑️ Rechazados por longitud: {rechazados} | 🚫 Descartados por confianza baja: {descartados_verificar}")

if not guiones_finales:
    # Nunca sobrescribir con cero filas: la FASE 5 hacía `mode("overwrite")`
    # y dejaba la tabla vacía, así que el run perdía también los guiones de
    # días anteriores y `generar_video` moría sin nada que producir.
    print("⚠️ Ningún guion válido en esta corrida: la tabla se conserva intacta.")
    dbutils.notebook.exit(json.dumps({
        "scrapeados": len(items),
        "candidatos": len(candidatos),
        "guiones": 0,
        "rechazados": rechazados,
        "descartados_verificar": descartados_verificar,
        "tabla": TABLA_GUIONES,
        "accion": "tabla_conservada",
    }))

# COMMAND ----------
# MAGIC %md
# MAGIC ## FASE 5: guardar en Delta (`tiktok.gold.guiones_60s`)

# COMMAND ----------

rows = []
for g in guiones_finales:
    textos = g.get("texto_en_pantalla", []) or []
    rows.append(Row(
        video_fuente_id=g["video_fuente_id"],
        video_fuente_url=g["video_fuente_url"],
        fuente_plays=g["video_fuente_plays"],
        hook=g.get("hook", ""),
        open_loop=g.get("open_loop", ""),
        bloque_1=g.get("bloque_1", ""),
        pattern_interrupt=g.get("pattern_interrupt", ""),
        bloque_2=g.get("bloque_2", ""),
        pre_cierre=g.get("pre_cierre", ""),
        cierre=g.get("cierre", ""),
        cta=g.get("cta", ""),
        hashtags=",".join(g.get("hashtags", []) or []),
        datos_a_verificar=" | ".join(str(d) for d in (g.get("datos_a_verificar", []) or [])),
        confianza=str(g.get("confianza", "")).strip().lower(),
        texto_pantalla_1=textos[0] if len(textos) > 0 else "",
        texto_pantalla_2=textos[1] if len(textos) > 1 else "",
        texto_pantalla_3=textos[2] if len(textos) > 2 else "",
        palabras_reales=g["palabras_reales"],
        duracion_estimada=g["duracion_estimada"],
        generado_en=datetime.utcnow(),
    ))

schema = StructType([
    StructField("video_fuente_id", StringType()),
    StructField("video_fuente_url", StringType()),
    StructField("fuente_plays", LongType()),
    StructField("hook", StringType()),
    StructField("open_loop", StringType()),
    StructField("bloque_1", StringType()),
    StructField("pattern_interrupt", StringType()),
    StructField("bloque_2", StringType()),
    StructField("pre_cierre", StringType()),
    StructField("cierre", StringType()),
    StructField("cta", StringType()),
    StructField("hashtags", StringType()),
    StructField("datos_a_verificar", StringType()),
    StructField("confianza", StringType()),
    StructField("texto_pantalla_1", StringType()),
    StructField("texto_pantalla_2", StringType()),
    StructField("texto_pantalla_3", StringType()),
    StructField("palabras_reales", LongType()),
    StructField("duracion_estimada", DoubleType()),
    StructField("generado_en", TimestampType()),
])

TABLA = TABLA_GUIONES
df_guiones = spark.createDataFrame(rows, schema)
# MERGE, no overwrite. El `overwrite` borraba en cada corrida los guiones que no
# volvían a salir del scrapeo, y con ellos la única fuente de `generar_video`:
# por eso no se podía rehacer un video antiguo, su guion ya no existía en
# ninguna tabla. El MERGE conserva el histórico y solo refresca el guion de un
# ID que se vuelva a generar.
if spark.catalog.tableExists(TABLA):
    df_guiones.createOrReplaceTempView("guiones_nuevos")
    spark.sql(f"""
        MERGE INTO {TABLA} AS destino
        USING guiones_nuevos AS origen
        ON destino.video_fuente_id = origen.video_fuente_id
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """)
    print(f"💾 {df_guiones.count()} guiones fusionados en {TABLA} (histórico conservado)")
else:
    df_guiones.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(TABLA)
    print(f"💾 {df_guiones.count()} guiones guardados en {TABLA}")

display(df_guiones.select("hook", "duracion_estimada", "palabras_reales", "hashtags", "fuente_plays").limit(5))

dbutils.notebook.exit(json.dumps({
    "scrapeados": len(items),
    "candidatos": len(candidatos),
    "guiones": len(guiones_finales),
    "rechazados": rechazados,
    "descartados_verificar": descartados_verificar,
    "tabla": TABLA,
}))
