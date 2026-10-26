"""Tests de la lógica de animación que no depende de renderizar video."""

import array
import math
import wave

import pytest

from scripts import jesucitos


def _audio_sintetico(ruta, patron):
    """Genera un wav de 1 s cuya amplitud sigue `patron(t)`.

    Se escribe con la stdlib en vez de pasar la señal a ffmpeg: una expresión
    `aevalsrc` con miles de muestras rebasa el límite de línea de comandos de
    Windows y el fixture falla antes de probar nada.
    """
    frecuencia = 8000
    muestras = array.array(
        "h", (int(patron(i / frecuencia) * 32767) for i in range(frecuencia))
    )
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(frecuencia)
        w.writeframes(muestras.tobytes())


def test_envolvente_frames_siguen_la_duracion(tmp_path):
    audio = tmp_path / "silencio.wav"
    _audio_sintetico(audio, lambda t: 0.5)

    energias = jesucitos.envolvente_audio(audio, fps=30)

    assert len(energias) == pytest.approx(30, abs=1)
    assert all(math.isfinite(e) for e in energias)


def test_envolvente_es_sensible_a_la_amplitud(tmp_path):
    """El primer medio segundo suena y el segundo calla."""
    audio = tmp_path / "mitad.wav"
    _audio_sintetico(audio, lambda t: 0.9 if t < 0.5 else 0.0)

    energias = jesucitos.envolvente_audio(audio, fps=30)

    assert max(energias[20:]) < 0.05
    assert min(energias[:12]) > 0.5


def test_suavizar_cae_mas_lento_de_lo_que_sube():
    """Ataque rápido / caída lenta: es lo que hace natural una boca."""
    suave = jesucitos.suavizar([1.0, 0.0, 0.0, 0.0])

    assert suave[0] == pytest.approx(0.45, abs=0.05)
    assert suave[1] > 0.1


def test_aperturas_recorta_el_silencio():
    aperturas = jesucitos.aperturas_boca([0.0, 0.05, 0.0, 0.04])

    assert all(a == 0.0 for a in aperturas)


def test_aperturas_no_se_salen_de_rango():
    aperturas = jesucitos.aperturas_boca([5.0] * 4)

    assert all(0.0 <= a <= 1.0 for a in aperturas)


def test_parpadeos_cubren_todo_el_clip():
    parpadeo = jesucitos.parpadeos(90, fps=30)

    assert len(parpadeo) == 90
    assert any(p > 0 for p in parpadeo)
    assert all(0.0 <= p <= 1.0 for p in parpadeo)


def test_personaje_cambia_con_la_apertura():
    """Si la apertura no cambia los píxeles, la boca no está animada."""
    cerrada = jesucitos.dibujar_personaje(0.0, 0.0, 0.0, 0.0, 0.5)
    abierta = jesucitos.dibujar_personaje(1.0, 0.0, 0.0, 0.0, 0.5)

    assert cerrada.size == (jesucitos.ANCHO_PJ, jesucitos.ALTO_PJ)
    assert cerrada.mode == "RGBA"
    assert cerrada.tobytes() != abierta.tobytes()


def test_personaje_tiene_transparencia_en_las_esquinas():
    """Si las esquinas no son transparentes, el personaje tapa el fondo."""
    img = jesucitos.dibujar_personaje(0.5, 0.0, 0.0, 0.0, 0.5)

    assert img.getpixel((0, 0))[3] == 0
    assert img.getpixel((img.width - 1, 0))[3] == 0


def test_fondo_procedural_tiene_el_tamano_del_frame():
    fondo = jesucitos.fondo_procedural()

    assert fondo.size == (jesucitos.ANCHO, jesucitos.ALTO)
    assert fondo.mode == "RGBA"


def test_frame_tiene_el_tamano_esperado():
    fondo = jesucitos.cargar_fondo(None)
    personaje = jesucitos.dibujar_personaje(0.5, 0.0, 0.0, 0.0, 0.5)

    crudo = jesucitos.componer_frame(fondo, personaje, t=0.0, duracion=5.0)

    assert len(crudo) == jesucitos.ANCHO * jesucitos.ALTO * 3


def test_animar_produce_un_mp4_reproducible(tmp_path):
    """Regresión del deadlock de stderr.

    Con stderr=PIPE sin lector, ffmpeg se bloqueaba a mitad del clip: el
    archivo quedaba sin átomo `moov` y `probe` devolvía duración None. Un test
    que solo mirara "el archivo existe" habría pasado con el render roto.
    """
    audio = tmp_path / "voz.wav"
    _audio_sintetico(audio, lambda t: 0.5)
    salida = tmp_path / "clip.mp4"

    jesucitos.animar(audio, None, salida)

    info = jesucitos.common.probe(salida)
    assert (info["ancho"], info["alto"]) == (jesucitos.ANCHO, jesucitos.ALTO)
    assert info["tiene_audio"] is True
    assert info["duracion"] is not None and 0.5 < info["duracion"] < 2.0
