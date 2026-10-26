import hashlib
import hmac

from slackbot.firma import firma_valida

SECRETO = "8f14e45fceea167a5a36dedd4bea2543"
CUERPO = b'{"type":"event_callback"}'
AHORA = 1_800_000_000.0


def firmar(ts: str, cuerpo: bytes) -> str:
    base = f"v0:{ts}:".encode() + cuerpo
    return "v0=" + hmac.new(SECRETO.encode(), base, hashlib.sha256).hexdigest()


def test_firma_correcta_y_reciente_pasa():
    ts = str(int(AHORA))
    assert firma_valida(SECRETO, ts, CUERPO, firmar(ts, CUERPO), ahora=AHORA)


def test_firma_con_cuerpo_distinto_falla():
    ts = str(int(AHORA))
    assert not firma_valida(SECRETO, ts, b"otro", firmar(ts, CUERPO), ahora=AHORA)


def test_firma_con_secreto_distinto_falla():
    ts = str(int(AHORA))
    base = f"v0:{ts}:".encode() + CUERPO
    ajena = "v0=" + hmac.new(b"otro", base, hashlib.sha256).hexdigest()
    assert not firma_valida(SECRETO, ts, CUERPO, ajena, ahora=AHORA)


def test_timestamp_viejo_falla_aunque_el_hmac_sea_correcto():
    # Fuera de la ventana de 5 minutos no vale, aunque el HMAC sea correcto.
    ts = str(int(AHORA - 301))
    assert not firma_valida(SECRETO, ts, CUERPO, firmar(ts, CUERPO), ahora=AHORA)


def test_el_limite_exacto_de_la_ventana_pasa():
    # 300 s justos está dentro; 301 no. Sin este caso, cambiar ">" por ">="
    # pasaría inadvertido.
    ts = str(int(AHORA - 300))
    assert firma_valida(SECRETO, ts, CUERPO, firmar(ts, CUERPO), ahora=AHORA)


def test_cabecera_ausente_no_lanza():
    # La ruta de Flask recibe None si Slack no manda la cabecera.
    assert not firma_valida(SECRETO, None, CUERPO, "v0=x", ahora=AHORA)
    assert not firma_valida(None, str(int(AHORA)), CUERPO, "v0=x", ahora=AHORA)


def test_timestamp_del_futuro_falla():
    # Reloj adelantado no debe abrir una ventana indefinida.
    ts = str(int(AHORA + 301))
    assert not firma_valida(SECRETO, ts, CUERPO, firmar(ts, CUERPO), ahora=AHORA)


def test_firma_ausente_o_mal_formada_falla():
    ts = str(int(AHORA))
    assert not firma_valida(SECRETO, ts, CUERPO, "", ahora=AHORA)
    assert not firma_valida(SECRETO, ts, CUERPO, "v0=zzz", ahora=AHORA)
    assert not firma_valida(SECRETO, "no-numero", CUERPO, firmar(ts, CUERPO), ahora=AHORA)
