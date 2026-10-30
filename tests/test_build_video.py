import pytest

from scripts.render import build_video
from scripts.util import common


def _video_sintetico(tmp_path, segundos=2, tam="1080x1920", con_audio=True):
    salida = tmp_path / f"v_{segundos}_{tam}_{con_audio}.mp4"
    args = ["-f", "lavfi", "-i", f"color=c=blue:s={tam}:d={segundos}"]
    if con_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={segundos}",
                 "-shortest", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-y", str(salida)]
    common.run_ffmpeg(args)
    return salida


def test_validar_salida_ok(tmp_path):
    v = _video_sintetico(tmp_path, segundos=2)
    build_video.validar_salida(v, duracion_esperada=None)


def test_validar_rechaza_sin_audio(tmp_path):
    v = _video_sintetico(tmp_path, segundos=2, con_audio=False)
    with pytest.raises(ValueError):
        build_video.validar_salida(v, duracion_esperada=None)


def test_validar_rechaza_resolucion_distinta(tmp_path):
    v = _video_sintetico(tmp_path, segundos=2, tam="720x1280")
    with pytest.raises(ValueError):
        build_video.validar_salida(v, duracion_esperada=None)


def test_menos_escenas_que_bloques_reutiliza_ultima(tmp_path):
    assert len(build_video.emparejar_escenas(["a", "b", "c", "d"], [{"ruta": "x"}])) == 4


def test_placeholder_incluye_texto_overlay(tmp_path):
    """Sin clip, la escena debe ser color CON el texto a pantalla.

    La spec prefiere texto sobre color antes que un hueco mudo: sin esto el
    video sale como bloques de color sin nada que leer.
    """
    args = build_video._args_escena(
        {"ruta": None, "texto_overlay": "Mendigar amor"}, duracion=4, indice=0,
        salida=tmp_path / "e0.mp4",
    )
    assert "drawtext" in " ".join(args)
    assert "Mendigar amor" in " ".join(args)


def test_texto_largo_se_parte_en_lineas():
    """1222 px en un frame de 1080: Arial 96 sin wrap se sale del video."""
    lineas = build_video._partir_texto("Mendigar amor no es amor", max_caracteres=16)
    assert len(lineas) >= 2
    assert all(len(l) <= 16 for l in lineas)


def test_escena_con_clip_tambien_dibuja_texto(tmp_path):
    """Una escena con b-roll no debe perder su texto a pantalla."""
    clip = tmp_path / "c.mp4"
    common.run_ffmpeg(["-f", "lavfi", "-i", "color=c=red:s=1080x1920:d=2",
                       "-c:v", "libx264", "-y", str(clip)])
    args = build_video._args_escena({"ruta": str(clip), "texto_overlay": "Hola"},
                                    duracion=2, indice=0, salida=tmp_path / "o.mp4")
    assert "drawtext" in " ".join(args)


def test_subs_ausente_es_error(tmp_path):
    v = tmp_path / "a.mp4"
    common.run_ffmpeg(["-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=2",
                       "-f", "lavfi", "-i", "sine=duration=2", "-shortest",
                       "-c:v", "libx264", "-c:a", "aac", "-y", str(v)])
    with pytest.raises(FileNotFoundError):
        build_video.construir_video(v, tmp_path / "no_existe.ass",
                                    [{"ruta": None, "texto_overlay": "x"}], tmp_path / "o.mp4")


def test_ruta_subs_absoluta_es_escapada():
    """`ass=C:/...` rompe el filtro: el ':' parte el nombre de filtro."""
    assert build_video._escapar_ruta_filtro("C:/x/subs.ass") == r"'C\:/x/subs.ass'"


def test_sin_escenas_es_error(tmp_path):
    v = tmp_path / "a.mp4"
    common.run_ffmpeg(["-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=2", "-c:v", "libx264", "-y", str(v)])
    with pytest.raises(ValueError):
        build_video.construir_video(v, None, [], tmp_path / "o.mp4")
