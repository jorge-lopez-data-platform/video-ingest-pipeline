# Databricks notebook source
# MAGIC %md
# MAGIC # Cola de subida: videos nuevos + pendientes de corridas anteriores
# MAGIC
# MAGIC Corre **antes** de `subir_borradores` y decide qué se va a subir en esta
# MAGIC corrida. Antes corría después y solo escribía una lista que nadie leía, así
# MAGIC que un video que se saltaba por el cupo del inbox no se reintentaba jamás.
# MAGIC
# MAGIC Dos fuentes, unidas sin duplicados:
# MAGIC
# MAGIC 1. Los videos recién generados por `generar_video` (taskValue `videos`).
# MAGIC 2. Los pendientes: `gold.guiones_usados` menos `bronze.tiktok_uploads`, es
# MAGIC    decir, lo generado que nunca llegó al inbox (o se saltó por cupo lleno).
# MAGIC
# MAGIC Los pendientes se recortan a `max_pendientes` por corrida: el inbox de TikTok
# MAGIC tiene un cupo de borradores sin publicar, y volcar todo de golpe lo vuelve a
# MAGIC llenar dejando el resto en `SKIPPED_INBOX_FULL`.
# MAGIC
# MAGIC La verdad se recalcula desde cero en cada corrida en vez de llevar un
# MAGIC contador, así un reintento nunca duplica un video ni pierde uno a medias.

# COMMAND ----------

import os
import sys

dbutils.widgets.text("max_pendientes", "20",
                     "Tope de videos pendientes a re-subir por corrida (0 = todos)")

GENERADOS = "tiktok.gold.guiones_usados"
SUBIDOS = "tiktok.bronze.tiktok_uploads"
AUDITORIA = "tiktok.default.upload_drafts"
VIDEOS_DIR = "/Volumes/tiktok/default/videos"
LIB = f"{VIDEOS_DIR}/lib"

# Los renders nuevos se agrupan en AAAA/MM/DD; sin esta resolucion el notebook
# solo ve la raiz y da por perdidos los videos de hoy.
sys.path.insert(0, LIB)
from scripts import rutas  # noqa: E402

try:
    MAX_PENDIENTES = max(0, int(dbutils.widgets.get("max_pendientes") or 0))
except ValueError:
    MAX_PENDIENTES = 20
    print("max_pendientes invalido; se usa 20.")

# COMMAND ----------

# MAGIC %md ## 1. Videos nuevos de esta corrida
# MAGIC
# MAGIC `seleccionar_guiones` los deja en su taskValue `videos`. Lo lee de ahí y
# MAGIC no de `generar_video` porque `generar_video` es un `for_each_task` y una
# MAGIC tarea for_each NO expone al padre los task values de sus iteraciones: leerla
# MAGIC devolvería siempre una lista vacía. Si la tarea no publicó nada, se sigue
# MAGIC solo con los pendientes: el orden de la lista no debe impedir drenar lo que
# MAGIC quedó atrás.

# COMMAND ----------

try:
    nuevos = dbutils.jobs.taskValues.get(
        taskKey="seleccionar_guiones", key="videos", default=[], debugValue=[]
    ) or []
except Exception as exc:
    print(f"AVISO: no se pudo leer seleccionar_guiones.values.videos "
          f"({type(exc).__name__}); sigo sin nuevos.")
    nuevos = []

print(f"Videos nuevos: {len(nuevos)}")
for n in nuevos:
    print("  +", n.get("video_name"))

# COMMAND ----------

# MAGIC %md ## 2. Pendientes de corridas anteriores

# COMMAND ----------

# No se puede resolver con un JOIN por nombre: `guiones_usados` guarda el nombre
# canonico ("X.mp4") y `tiktok_uploads` guarda el que recibio la tarea
# ("X_v2.mp4"). Al no coincidir, un video ya subido seguia contando como
# pendiente para siempre y se re-subia en cada corrida gastando el cupo.
# Se traen ambas listas y se comparan normalizadas en Python.


def canonico(nombre):
    """Nombre sin el sufijo de version del render: 'x_v2.mp4' -> 'x.mp4'."""
    base, ext = os.path.splitext(nombre)
    return f"{base[:-3]}{ext}" if base.endswith("_v2") else nombre


# Hay DOS tablas de subidas y no coinciden: `tiktok_uploads` (bronze) y
# `upload_drafts` (la auditoría que escribe el subidor). El 2026-10-25 bronze
# tenía 7 subidas en 24 h y la auditoría 25. Leer solo bronze hacía que 18
# videos ya enviados siguieran contando como pendientes y se re-encolaran;
# uno de ellos acabó en `ValueError: This exact file was already sent`.
# Se unen las dos: un video está subido si aparece en cualquiera.
SUBIDAS = (SUBIDOS, AUDITORIA)

ya_subidos = {
    canonico(fila["video_name"])
    for tabla in SUBIDAS
    for fila in spark.sql(
        f"""SELECT DISTINCT video_name FROM {tabla}
            WHERE status IN ('SEND_TO_USER_INBOX', 'PUBLISH_COMPLETE')"""
    ).collect()
}

# Re-encolar lo que ya se saltó por cupo dentro de la misma ventana de 24 h no
# sirve de nada: el cupo de TikTok es móvil y no se libera hasta que el dueño
# publica o borra un borrador. Peor aún, ese reintento es lo que llenaba la cola
# de peticiones que TikTok marcó como ráfaga (14 `init` en 4 minutos el
# 2026-10-24, con solo 2 aceptados). Se descartan hasta que la ventana avance.
saltados_recientes = {
    canonico(fila["video_name"])
    for fila in spark.sql(
        f"""SELECT DISTINCT video_name FROM {SUBIDOS}
            WHERE status = 'SKIPPED_INBOX_FULL'
              AND uploaded_at >= current_timestamp() - INTERVAL 24 HOURS"""
    ).collect()
}

# Un video puede estar en bronze como SKIPPED_INBOX_FULL y a la vez en la
# auditoría como SEND_TO_USER_INBOX: se saltó por cupo en una corrida y se subió
# en otra (el 2026-10-25 había 20 casos así). Si se cuenta como saltado, el
# video se queda fuera de la cola aunque ya esté enviado. Manda la subida real.
saltados_recientes -= ya_subidos

if saltados_recientes:
    print(f"Omitidos por saltarse hace <24 h: {len(saltados_recientes)}")

# Los más recientes primero: los antiguos que ya fallaron tres veces son los que
# se quedaban atascados gastando el cupo de los nuevos.
pendientes = [
    fila
    for fila in spark.sql(
        f"""SELECT video_name, CAST(video_fuente_id AS STRING) AS video_fuente_id
            FROM {GENERADOS} ORDER BY generado_en DESC"""
    ).collect()
    if canonico(fila["video_name"]) not in ya_subidos
    and canonico(fila["video_name"]) not in saltados_recientes
]

# Un video recién generado ya aparece como "pendiente" (está en guiones_usados y
# todavía no en tiktok_uploads). Se descarta aquí para no subirlo dos veces.
ya_incluidos = {
    canonico(n["video_name"]) for n in nuevos if n.get("video_name")
}

# Hay dos eras de nombres en el volumen: los renders antiguos se guardaron como
# "<base>_v2.mp4" y `guiones_usados` se reescribió con el nombre canónico, sin
# sufijo. Buscar solo el nombre exacto descartaba TODOS los pendientes.
# Se resuelve probando el nombre exacto y luego el sufijo, comprobando siempre
# que el fichero exista: no se adivina, se verifica.
def resolver(nombre):
    """Nombre real del fichero en el volumen, o None si no está.

    Se apoya en `rutas.resolver`, que mira la raíz y las carpetas AAAA/MM/DD:
    los renders nuevos ya no viven planos, así que listar solo la raíz
    escondía todo lo generado desde el cambio de estructura.
    """
    for candidato in (nombre, _con_sufijo_inverso(nombre), _con_sufijo(nombre)):
        if candidato and rutas.resolver(candidato, VIDEOS_DIR):
            return candidato
    return None


def _con_sufijo_inverso(nombre):
    """"x_v2.mp4" -> "x.mp4" (el sufijo estaba puesto y no correspondía)."""
    base, ext = os.path.splitext(nombre)
    return f"{base[:-3]}{ext}" if base.endswith("_v2") else None


def _con_sufijo(nombre):
    """"x.mp4" -> "x_v2.mp4" (el fichero se guardó con sufijo)."""
    base, ext = os.path.splitext(nombre)
    return f"{base}_v2{ext}"


candidatos = []
vistos = set()          # por nombre real: evita encolar dos veces el mismo fichero
for fila in pendientes:
    nombre = fila["video_name"]
    if canonico(nombre) in ya_incluidos:
        continue
    real = resolver(nombre)
    if not real:
        # Se llega aquí cuando `guiones_usados` registró el guion pero el render
        # nunca se escribió (o se borró). El 2026-10-25 esto encoló
        # `motivacional_7690499505489333526.mp4`, que no existía en el volumen, y
        # la subida murió con FileNotFoundError.
        print(f"AVISO: {nombre} no esta en el volumen (ni con sufijo); se ignora.")
        continue
    # Doble comprobación contra el sistema de ficheros: `resolver` ya miró, pero
    # entre esa consulta y la subida puede pasar tiempo y el volumen es remoto.
    if not rutas.resolver(real, VIDEOS_DIR):
        print(f"AVISO: {nombre} desaparecio del volumen; se ignora.")
        continue
    # "X.mp4" y "X_v2.mp4" pueden estar las dos en guiones_usados y resolver al
    # mismo fichero; se encola una sola vez.
    if real in vistos:
        continue
    vistos.add(real)
    if real != nombre:
        print(f"  (nombre resuelto: {nombre} -> {real})")
    candidatos.append({"video_name": real})

total_pendientes = len(candidatos)
if MAX_PENDIENTES and total_pendientes > MAX_PENDIENTES:
    print(f"AVISO: {total_pendientes} pendientes; se re-suben solo "
          f"{MAX_PENDIENTES} en esta corrida.")
    candidatos = candidatos[:MAX_PENDIENTES]

print(f"Pendientes a re-subir: {len(candidatos)} de {total_pendientes} totales")
for p in candidatos:
    print("  ~", p["video_name"])

# COMMAND ----------

# MAGIC %md ## 3. Cola final para `subir_borradores`
# MAGIC
# MAGIC `subir_borradores` consume este `videos` en su `for_each`. Si la celda que lo
# MAGIC escribe no está separada por `# COMMAND ----------`, Databricks mete el texto
# MAGIC `# MAGIC %md ...` dentro del mismo bloque Python: esas líneas se ejecutan como
# MAGIC comentarios y la celda termina bien, pero el `taskValues.set` de abajo queda
# MAGIC dentro del mismo cuerpo y sí corre. El fallo real es el contrario: una celda
# MAGIC `%md` sin separador deja el `set()` pegado a un bloque que en realidad corre
# MAGIC como markdown si el separador existe. Se dejan los separadores explícitos
# MAGIC para que cada celda tenga un único tipo.

# COMMAND ----------

# `nuevos` viene de `seleccionar_guiones` y NO pasaba por `resolver`: se añadía a
# la cola tal cual. El 2026-10-25 eso encoló tres videos que no existían en el
# volumen (`7689607819489791250`, `7690499505489333526`, `7699804340013354262`);
# el subidor los saltó en verde, pero la cola quedó ocupada por fantasmas y con
# `max_pendientes` bajo eso desplazaba videos reales.
nuevos_existentes = []
for n in nuevos:
    nombre = (n or {}).get("video_name")
    if not nombre:
        continue
    if not rutas.resolver(nombre, VIDEOS_DIR):
        print(f"AVISO: {nombre} (nuevo) no está en el volumen; se ignora.")
        continue
    nuevos_existentes.append(n)
if len(nuevos_existentes) != len(nuevos):
    print(f"Nuevos descartados por no existir: {len(nuevos) - len(nuevos_existentes)}")

cola = list(nuevos_existentes) + list(candidatos)
print(f"Total a subir en esta corrida: {len(cola)}")

dbutils.jobs.taskValues.set(key="videos", value=cola)
dbutils.jobs.taskValues.set(key="nuevos", value=len(nuevos_existentes))
dbutils.jobs.taskValues.set(key="pendientes", value=len(candidatos))
dbutils.jobs.taskValues.set(key="pendientes_totales", value=total_pendientes)

dbutils.notebook.exit(str(len(cola)))
