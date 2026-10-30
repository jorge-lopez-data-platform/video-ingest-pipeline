"""Tests del cupo de borradores pendientes de TikTok."""

from datetime import datetime, timedelta, timezone

from scripts.util.cuota import (
    LIMITE_24H,
    cuantas_caben,
    hay_cupo,
    usadas_en_ventana,
)

AHORA = datetime(2026, 10, 25, 12, 0, 0)


def hace(horas: float) -> datetime:
    return AHORA - timedelta(hours=horas)


def test_sin_subidas_previas_hay_cupo_completo():
    assert usadas_en_ventana([], AHORA) == 0
    assert hay_cupo([], AHORA) is True
    assert cuantas_caben([], AHORA) == LIMITE_24H


def test_cuenta_solo_lo_que_cae_dentro_de_la_ventana():
    fechas = [hace(1), hace(23), hace(25), hace(48)]
    assert usadas_en_ventana(fechas, AHORA) == 2


def test_justo_en_el_limite_no_hay_cupo():
    fechas = [hace(h) for h in range(1, LIMITE_24H + 1)]
    assert hay_cupo(fechas, AHORA) is False
    assert cuantas_caben(fechas, AHORA) == 0


def test_una_por_debajo_del_limite_si_hay_cupo():
    fechas = [hace(h) for h in range(1, LIMITE_24H)]
    assert hay_cupo(fechas, AHORA) is True
    assert cuantas_caben(fechas, AHORA) == 1


def test_las_filas_sin_fecha_no_bloquean():
    # Un None cuenta como desconocido: contarlo cerraría el cupo para siempre.
    assert usadas_en_ventana([None, None], AHORA) == 0
    assert hay_cupo([None] * 50, AHORA) is True


def test_una_fecha_futura_no_cuenta():
    # Reloj desajustado o fila corrupta: no debe consumir cupo del presente.
    assert usadas_en_ventana([AHORA + timedelta(hours=2)], AHORA) == 0


def test_la_ventana_es_movil_no_de_calendario():
    # Cinco subidas de ayer, pero todas con más de 24 h: el cupo está libre.
    fechas = [hace(25), hace(26), hace(27), hace(28), hace(29)]
    assert hay_cupo(fechas, AHORA) is True


def test_el_borde_de_la_ventana_cuenta():
    # Exactamente 24 h: queda fuera (la ventana es estrictamente mayor que desde).
    assert usadas_en_ventana([hace(24)], AHORA) == 0
    assert usadas_en_ventana([hace(23.99)], AHORA) == 1


def test_cuantas_caben_nunca_es_negativo():
    fechas = [hace(h) for h in range(1, LIMITE_24H + 10)]
    assert cuantas_caben(fechas, AHORA) == 0


def test_25_subidas_en_24h_ya_no_bloquean():
    # El 2026-10-25 TikTok aceptó 25 `init` en 24 h con el dueño publicando a
    # mano. Con el límite rígido de 5 el cortacircuitos habría frenado subidas
    # que la API aceptaba.
    ahora = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
    fechas = [datetime(2026, 10, 25, h, 0, 0) for h in range(0, 12)]
    assert usadas_en_ventana(fechas, ahora) == 12
    assert hay_cupo(fechas, ahora) is True


def test_spark_naive_contra_ahora_aware_no_revienta():
    # Regresión del 2026-10-24: Spark devuelve `uploaded_at` sin zona y el
    # subidor pasaba `datetime.now(timezone.utc)`. La comparación lanzaba
    # `TypeError: can't compare offset-naive and offset-aware datetimes` y el
    # cortacircuitos moría en vez de saltarse la subida.
    ahora = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
    naive = [datetime(2026, 10, 25, 11, 0, 0)]  # 1 h antes, sin tzinfo
    assert usadas_en_ventana(naive, ahora) == 1
    assert hay_cupo(naive * LIMITE_24H, ahora) is False


def test_mezcla_de_naive_y_aware_se_cuenta_junta():
    ahora = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
    fechas = [
        datetime(2026, 10, 25, 11, 0, 0),                       # naive, dentro
        datetime(2026, 10, 25, 9, 0, 0, tzinfo=timezone.utc),   # aware, dentro
        datetime(2026, 10, 24, 8, 0, 0),                        # naive, fuera
    ]
    assert usadas_en_ventana(fechas, ahora) == 2


def test_aware_en_otra_zona_se_convierte_a_utc():
    ahora = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
    # 13:00 en UTC+2 son las 11:00 UTC: dentro de la ventana.
    madrid = timezone(timedelta(hours=2))
    assert usadas_en_ventana(
        [datetime(2026, 10, 25, 13, 0, 0, tzinfo=madrid)], ahora) == 1
