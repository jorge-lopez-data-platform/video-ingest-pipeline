# Databricks notebook source
# MAGIC %md
# MAGIC # Selección aleatoria de hashtags (sin repetición entre videos)
# MAGIC
# MAGIC Se sortean `n` hashtags de un catálogo por temática más un fondo genérico.
# MAGIC El historial vive en `tiktok.gold.hashtags_usados`: los que aparecen en los
# MAGIC últimos `ventana` usos se excluyen, de modo que un hashtag no se repite hasta
# MAGIC que la ventana se desplaza. Si el catálogo se queda corto se relaja la
# MAGIC exclusión en lugar de fallar: es preferible repetir a no producir el video.

# COMMAND ----------

import random

TABLA_HASHTAGS = "tiktok.gold.hashtags_usados"

# Temáticas conocidas. Las claves se comparan sin `#` y en minúsculas, así que
# `hashtags=amor,duelo,reflexion` enciende los tres bloques.
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

# Fondo común: sirve para completar cuando la temática no aporta suficientes.
GENERICOS = ["fyp", "parati", "viral", "foryou", "tendencia", "explorar",
             "frases", "consejos", "aprendeentiktok", "inspiraciondiaria",
             "reflexionesdiarias", "crecimientopersonal", "consejosdevida",
             "parati_viral", "vidademotivacion"]

# Cuántos usos recientes se miran para decidir si un hashtag está "gastado".
VENTANA = 80


def _normalizar(tema):
    return tema.strip().lower().lstrip("#").replace(" ", "")


def construir_pool(temas):
    """Devuelve la lista de hashtags candidatos para los temas pedidos.

    Los temas se buscan en el catálogo; si un tema no está, se usa tal cual como
    hashtag (permite `hashtags=playa,atardecer` sin tocar el código).
    """
    pool, vistos = [], set()

    def añadir(lista):
        for h in lista:
            clave = _normalizar(h)
            if clave and clave not in vistos:
                vistos.add(clave)
                pool.append(clave)

    for tema in temas:
        clave = _normalizar(tema)
        if clave in CATALOGO:
            añadir(CATALOGO[clave])
        elif clave:
            añadir([clave])
    añadir(GENERICOS)
    return pool


def cargar_usados(ventana=VENTANA):
    """Hashtags usados en los últimos `ventana` videos, o set() si no hay tabla."""
    try:
        filas = spark.sql(
            f"""SELECT hashtags FROM {TABLA_HASHTAGS}
                ORDER BY generado_en DESC LIMIT {int(ventana)}"""
        ).collect()
        usados = set()
        for r in filas:
            for h in (r["hashtags"] or "").split(","):
                if h.strip():
                    usados.add(_normalizar(h))
        return usados
    except Exception as exc:
        print(f"Historial de hashtags no legible ({type(exc).__name__}); se sortea sin él.")
        return set()


def elegir(pool, usados, n=5, ya_en_run=()):
    """Sortea `n` hashtags distintos, evitando los usados y los del propio run.

    Si el pool filtrado no alcanza, se relaja el filtro por capas en vez de
    devolver menos de `n` o fallar.
    """
    excluir_run = {_normalizar(h) for h in ya_en_run}
    disponibles = [h for h in pool if h not in usados and h not in excluir_run]
    if len(disponibles) < n:
        print(f"AVISO: solo {len(disponibles)} hashtags libres; se relaja el historial.")
        disponibles = [h for h in pool if h not in excluir_run]
    if len(disponibles) < n:
        print(f"AVISO: pool insuficiente ({len(disponibles)}); se permite repetir del run.")
        disponibles = list(pool)

    return random.sample(disponibles, min(n, len(disponibles)))


def registrar(video_fuente_id, video_name, hashtags):
    """Añade los hashtags elegidos al historial. Un fallo no debe tumbar el video."""
    try:
        from datetime import datetime, timezone
        from pyspark.sql import Row
        from pyspark.sql.types import StringType, StructField, StructType, TimestampType

        spark.sql(
            f"""CREATE TABLE IF NOT EXISTS {TABLA_HASHTAGS} (
                  video_fuente_id STRING, video_name STRING, hashtags STRING,
                  generado_en TIMESTAMP
                ) USING DELTA"""
        )
        esquema = StructType([
            StructField("video_fuente_id", StringType()),
            StructField("video_name", StringType()),
            StructField("hashtags", StringType()),
            StructField("generado_en", TimestampType()),
        ])
        fila = Row(video_fuente_id=str(video_fuente_id), video_name=video_name,
                   hashtags=",".join(hashtags),
                   generado_en=datetime.now(timezone.utc))
        spark.createDataFrame([fila], esquema).write.mode("append").saveAsTable(TABLA_HASHTAGS)
        print(f"Hashtags registrados en {TABLA_HASHTAGS}: {','.join(hashtags)}")
    except Exception as exc:
        print(f"AVISO: no se pudo registrar el historial de hashtags ({type(exc).__name__}): {exc}")
