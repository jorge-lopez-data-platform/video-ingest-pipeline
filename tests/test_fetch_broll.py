import pytest

from scripts.util import fetch_broll


def test_usa_local_antes_que_pexels(tmp_path, monkeypatch):
    (tmp_path / "a.mp4").write_bytes(b"x")
    monkeypatch.setattr(fetch_broll, "_pexels", lambda *_: pytest.fail("no debe llamarse"))
    escenas = fetch_broll.resolver_escenas({"texto_en_pantalla": ["uno"]}, tmp_path, "key")
    assert escenas[0]["fuente"] == "local"


def test_sin_nada_cae_en_placeholder(tmp_path):
    escenas = fetch_broll.resolver_escenas({"texto_en_pantalla": ["uno", "dos"]}, tmp_path, None)
    assert all(e["fuente"] == "placeholder" for e in escenas)
    assert escenas[0]["texto_overlay"] == "uno"


def test_numero_de_escenas_cubre_el_video(tmp_path):
    escenas = fetch_broll.resolver_escenas(
        {"texto_en_pantalla": []}, tmp_path, None, duracion_total=60, por_escena=4
    )
    assert len(escenas) == 15
