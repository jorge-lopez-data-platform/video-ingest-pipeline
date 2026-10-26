"""Cupo de borradores pendientes del inbox de TikTok.

TikTok documenta, en la referencia de `/post/publish/inbox/video/init/`, que
«there may be at most 5 pending shares within any 24-hour period». Al pasarse,
el endpoint responde `spam_risk_too_many_pending_share` (HTTP 403).

**Lo que limita son los pendientes sin publicar, no las subidas.** El 2026-10-25
quedó medido: 25 `init` aceptados en 24 h, con 25 `publish_id` distintos. El
dueño publica los borradores a mano cada día, y eso libera el cupo. Por eso un
contador rígido de 5 subidas/24 h bloqueaba subidas que TikTok sí aceptaba.

El módulo sirve para tres cosas, en este orden de importancia:

1. **No hacer ráfagas.** Espaciar las subidas de una corrida es lo que evita
   que TikTok lea el patrón como abuso automatizado.
2. **Estimar la presión** sobre el inbox con un techo holgado y configurable.
3. **No reintentar** un error determinista: reintentar un `spam_risk_*` es
   justo la ráfaga que provoca el bloqueo.

Cuando TikTok dice que no hay cupo, la fuente de verdad es su respuesta, no
este contador: el subidor para la cola al recibir `spam_risk_*`.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

# Techo documentado por TikTok para borradores *pendientes* (no subidas).
LIMITE_PENDIENTES = 5

# Techo de subidas por ventana móvil. Es una estimación de presión, no el
# límite de TikTok: se deja holgado para no bloquear subidas que la API acepta.
# Medido el 2026-10-25: 25 aceptadas en 24 h con el dueño publicando a mano.
LIMITE_24H = 20

VENTANA_HORAS = 24

# Separación mínima entre dos `init` de la misma corrida. TikTok permite 6
# peticiones/minuto por token; 10 s deja margen de sobra y rompe el patrón de
# ráfaga sin alargar la cola de forma insensata.
SEGUNDOS_ENTRE_SUBIDAS = 10


def como_utc(f: datetime) -> datetime:
    """Normaliza a UTC con zona.

    Spark devuelve los `timestamp` de una tabla como datetime **naive**, mientras
    que quien llama suele pasar `datetime.now(timezone.utc)`. Compararlos lanza
    `TypeError: can't compare offset-naive and offset-aware datetimes`, que es lo
    que rompió el cortacircuitos en la primera corrida real. Un naive se asume
    UTC: así es como Databricks almacena `timestamp`.
    """
    if f.tzinfo is None:
        return f.replace(tzinfo=timezone.utc)
    return f.astimezone(timezone.utc)


def usadas_en_ventana(fechas, ahora: datetime, horas: int = VENTANA_HORAS) -> int:
    """Cuántas de esas subidas caen dentro de la ventana móvil que acaba en `ahora`.

    `fechas` puede traer `None` (filas antiguas sin marca de tiempo): se ignoran,
    porque contarlas como recientes bloquearía la subida para siempre.
    """
    ahora = como_utc(ahora)
    desde = ahora - timedelta(hours=horas)
    return sum(1 for f in fechas
               if f is not None and desde < como_utc(f) <= ahora)


def hay_cupo(fechas, ahora: datetime, limite: int = LIMITE_24H,
             horas: int = VENTANA_HORAS) -> bool:
    """True si todavía cabe al menos una subida más en la ventana."""
    return usadas_en_ventana(fechas, ahora, horas) < limite


def cuantas_caben(fechas, ahora: datetime, limite: int = LIMITE_24H,
                  horas: int = VENTANA_HORAS) -> int:
    """Cuántas subidas más admite la ventana ahora mismo (nunca negativo)."""
    return max(0, limite - usadas_en_ventana(fechas, ahora, horas))


def esperar_entre_subidas(segundos: int = SEGUNDOS_ENTRE_SUBIDAS) -> None:
    """Pausa antes del siguiente `init` para no formar una ráfaga.

    Se aplica entre subidas de la misma corrida. Es la contramedida directa al
    patrón que TikTok marcó como spam: 14 `init` en 4 minutos el 2026-10-24.
    """
    time.sleep(segundos)
