"""Fase serial del lote: qué guiones se producen y con qué cara y hashtags.

Todo lo de aquí corre **una sola vez** por lote, antes de repartir el trabajo
entre las iteraciones paralelas de `generar_video`. Ese reparto es la razón de
que este módulo exista: la selección, el diseño del personaje y el sorteo de
hashtags dependen de lo ya elegido en el mismo lote (no repetir cara, no repetir
hashtag), y ese estado no se puede mantener dentro de N procesos aislados.
Si cada iteración eligiera por su cuenta, las tres elegirían el mismo guion
"mejor" y los tres videos saldrían con la misma cara y los mismos hashtags.

Nada de aquí llama a `spark` por su cuenta: las funciones reciben la sesión y
devuelven valores, para poder probarlas con una sesión falsa.
"""

from __future__ import annotations

import json
import random

TABLA = "tiktok.gold.guiones_60s"
TABLA_USADOS = "tiktok.gold.guiones_usados"
TABLA_PERSONAJES = "tiktok.gold.personajes_usados"
TABLA_HASHTAGS = "tiktok.gold.hashtags_usados"

# La tabla se sobrescribe en cada corrida del job de guiones, así que las
# versiones anteriores son la única copia de los guiones ya producidos.
VERSION_MINIMA = 7  # desde aquí la tabla tiene las 20 columnas

COLUMNAS = ("video_fuente_id, hook, open_loop, bloque_1, pattern_interrupt, "
            "bloque_2, pre_cierre, cierre, cta, hashtags, datos_a_verificar, "
            "confianza, palabras_reales, duracion_estimada, texto_pantalla_1")

VENTANA_HASHTAGS = 80
LIMITE_FICHAS = 60


def ids_ya_usados(spark, tabla: str = TABLA_USADOS) -> set[str]:
    """IDs que ya produjeron un video, según el registro persistente.

    Se lee de una tabla y no de un widget porque un widget solo dura una
    corrida: sin persistencia, cada ejecución elegiría el mismo guion.
    """
    try:
        return {str(r["video_fuente_id"])
                for r in spark.sql(f"SELECT video_fuente_id FROM {tabla}").collect()}
    except Exception as exc:
        print(f"Registro {tabla} no legible ({type(exc).__name__}); se asume vacío.")
        return set()


def leer_guiones(spark, pedido: str = "", desde_historial: bool = False,
                 tabla: str = TABLA) -> list[dict]:
    """Guiones de la tabla (y de su historial, si se pide), uno por `video_fuente_id`.

    Sin `desde_historial` solo se ve la versión viva, que contiene únicamente los
    guiones de la última corrida del job de guiones. Con él se recorren las
    versiones antiguas y se queda el guion más reciente de cada ID, que es el que
    produjo el video que hay en disco.
    """
    filtro = f"WHERE video_fuente_id = '{pedido}'" if pedido else ""
    filas = [f.asDict() for f in spark.sql(
        f"SELECT {COLUMNAS} FROM {tabla} {filtro} ORDER BY generado_en DESC").collect()]

    if not desde_historial:
        return filas

    # Las versiones anteriores a la 7 nacieron con un esquema más corto y
    # consultarlas rompe el UNION. Se ordena de la más nueva a la más vieja para
    # que el desempate se quede siempre con el guion más reciente.
    versiones = sorted(
        (r["version"] for r in spark.sql(f"DESCRIBE HISTORY {tabla}").collect()
         if r["version"] >= VERSION_MINIMA),
        reverse=True,
    )
    bloques = [f"SELECT {v} AS v, {COLUMNAS} FROM {tabla} VERSION AS OF {v}"
               for v in versiones]
    recuperados = spark.sql(f"""
        SELECT {COLUMNAS} FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY video_fuente_id ORDER BY v DESC) rn
          FROM ({' UNION ALL '.join(bloques)})
        ) WHERE rn = 1
    """).collect()
    ya = {str(f["video_fuente_id"]) for f in filas}
    nuevos = [f.asDict() for f in recuperados if str(f["video_fuente_id"]) not in ya]
    print(f"Historial: {len(versiones)} versiones, {len(nuevos)} guiones recuperados")
    return filas + nuevos


def elegir(filas: list[dict], excluir: set[str], cantidad: int,
           sin_verificar: bool = False, pedido: str = "") -> list[dict]:
    """Los `cantidad` guiones que se van a producir, mejores primero.

    `pedido` fuerza un único ID explícito y salta el filtro de excluidos: se usa
    para regenerar un video concreto sin que el registro de usados lo estorbe.
    """
    if not filas:
        raise ValueError(f"La tabla {TABLA} está vacía. Ejecuta antes el job de guiones.")
    if pedido:
        return filas[:1]

    candidatos = [f for f in filas if str(f["video_fuente_id"]) not in excluir]
    if sin_verificar:
        limpias = candidatos
    else:
        # El prompt obliga al LLM a listar en `datos_a_verificar` cada cifra,
        # nombre o lugar, así que exigir esa lista vacía dejaba fuera a casi
        # todos los guiones. Decide la confianza que declara el propio modelo.
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
    ordenados = sorted(
        limpias,
        key=lambda f: (f.get("confianza") != "alta",
                       abs((f.get("duracion_estimada") or 60.0) - 60.0)),
    )
    elegidos = ordenados[:cantidad]
    if len(elegidos) < cantidad:
        print(f"AVISO: se pidieron {cantidad} videos y solo hay {len(elegidos)} guiones "
              f"utilizables. Se generarán {len(elegidos)}.")
    return elegidos


# --- fichas de personaje ----------------------------------------------------

def cargar_fichas(spark, tabla: str = TABLA_PERSONAJES,
                  limite: int = LIMITE_FICHAS) -> tuple[list[dict], dict[str, dict]]:
    """Historial de caras usadas y la ficha que ya corresponde a cada guion.

    Devuelve `(fichas_previas, fichas_por_id)`. La segunda sirve para que
    regenerar un video devuelva la MISMA cara: si se dejara al LLM, el "nuevo"
    video del mismo guion saldría con otro personaje y no sería una
    regeneración sino otro video.
    """
    previas, por_id = [], {}
    try:
        filas = spark.sql(
            f"SELECT video_fuente_id, firma, spec FROM {tabla} "
            f"ORDER BY generado_en DESC LIMIT {limite}"
        ).collect()
        previas = [json.loads(r["spec"]) for r in filas]
        for r in filas:
            por_id.setdefault(str(r["video_fuente_id"]), json.loads(r["spec"]))
    except Exception as exc:
        # La tabla no existe en la primera corrida; no debe impedir el render.
        print(f"Registro {tabla} no legible ({type(exc).__name__}); "
              "se diseña sin historial.")
    return previas, por_id


def crear_cliente(dbutils):
    """Cliente de OpenRouter para diseñar personajes."""
    from openai import OpenAI

    return OpenAI(
        api_key=dbutils.secrets.get("tiktok_pipeline", "openrouter_api_key"),
        base_url="https://openrouter.ai/api/v1",
        default_headers={"HTTP-Referer": "https://dbc-e83fc5f3-f80b.cloud.databricks.com",
                         "X-Title": "tiktok-personaje"},
    )


# --- hashtags ---------------------------------------------------------------

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

# Fondo común. Es amplio a propósito: con 5 videos por corrida hacen falta 25
# hashtags distintos, y un pool corto obligaría a repetir.
GENERICOS = ["fyp", "parati", "viral", "foryou", "tendencia", "explorar",
             "frases", "consejos", "aprendeentiktok", "inspiraciondiaria",
             "reflexionesdiarias", "crecimientopersonal", "consejosdevida",
             "parati_viral", "vidademotivacion", "crecimientopersonal",
             "frasesmotivadoras", "reflexionesdelavida", "pensamientospositivos",
             "leccionesdevida", "parati_viral", "vidaconproposito",
             "aprendedetiktok", "frasesparati", "momentosdevida",
             "pensandomejor", "sabiasque", "paratodos", "comparte",
             "vidareal", "reflexiona", "parati_", "crecimientoreal"]


def _norm_ht(tema: str) -> str:
    return tema.strip().lower().lstrip("#").replace(" ", "")


def construir_pool(temas: list[str]) -> list[str]:
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


def cargar_historial(spark, tabla: str = TABLA_HASHTAGS,
                     ventana: int = VENTANA_HASHTAGS) -> dict[str, int]:
    """Devuelve {hashtag: posición}, donde 0 es el uso más reciente.

    No se guarda un simple conjunto de "usados": el catálogo es finito y mucho
    menor que ventana*videos, así que una exclusión binaria se relajaría siempre.
    Con la posición se prefiere el menos usado recientemente, que equivale a no
    repetir mientras el pool alcance.
    """
    try:
        filas = spark.sql(
            f"SELECT hashtags FROM {tabla} ORDER BY generado_en DESC LIMIT {ventana}"
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


def sortear_hashtags(pool: list[str], historial: dict[str, int], n: int,
                     ya_en_run: list[str]) -> list[str]:
    """`n` hashtags distintos, priorizando los que llevan más tiempo sin salir.

    `historial` se actualiza in situ tras cada sorteo, de modo que los videos de
    una misma corrida tampoco comparten hashtags.
    """
    candidatos = [h for h in pool if h not in ya_en_run]
    if len(candidatos) < n:
        # Solo puede pasar con un catálogo diminuto en un run muy grande.
        print(f"AVISO: catálogo insuficiente ({len(candidatos)} libres para {n}); "
              "el run repetirá algún hashtag.")
        candidatos = list(pool)

    # Menos usados primero; los que nunca salieron (sin entrada) van delante.
    # El desempate es aleatorio: sin él, el orden del catálogo decidiría siempre
    # los mismos y los últimos hashtags del pool no se elegirían nunca.
    candidatos.sort(key=lambda h: (historial.get(h, -1), random.random()))
    elegidos_ht = candidatos[:n]

    for h in historial:
        historial[h] += 1
    for h in elegidos_ht:
        historial[h] = 0
    return elegidos_ht


def sortear_para(spark, temas: list[str], n: int, ids: list[str],
                 tabla: str = TABLA_HASHTAGS) -> dict[str, list[str]]:
    """Asigna `n` hashtags a cada ID del lote, sin repetir dentro del lote."""
    pool = construir_pool(temas)
    historial = cargar_historial(spark, tabla)
    print(f"Hashtags: {n} por video | temas={temas} | pool={len(pool)} | "
          f"en historial={len(historial)}")

    asignados: dict[str, list[str]] = {}
    ya_en_run: list[str] = []
    for vid in ids:
        elegidos_ht = sortear_hashtags(pool, historial, n, ya_en_run)
        ya_en_run.extend(elegidos_ht)
        asignados[vid] = elegidos_ht
        print(f"  {vid}: {','.join(elegidos_ht)}")
    return asignados
