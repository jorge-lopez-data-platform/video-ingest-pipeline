"""Anima un personaje de dibujo sobre un paisaje de paraíso, con la boca sincronizada.

El personaje se dibuja por capas con Pillow en vez de generarse con IA: no hay
en esta máquina un modelo de segmentación que separe un personaje del fondo de
una imagen generada, así que las capas son la única vía para mover la boca al
ritmo del audio. El fondo sí puede venir de una imagen IA (--fondo); si no se
indica, se genera uno procedural para que el video nunca salga vacío.

La amplitud real del audio decide cuánto abre la boca. Cualquier animación que
no escuche el audio da la sensación de doblaje desincronizado, que es peor que
una boca que se mueve mal.
"""

from __future__ import annotations

import argparse
import array
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from scripts.util import common

ANCHO, ALTO = 1080, 1920
FPS = 30
CRF = 20
# `veryfast` frente al `medium` por defecto: en un clip de 60 s a 1080x1920 el
# encode baja ~35% y a crf 20 la diferencia no se distingue. El x264 es la mayor
# parte del tiempo por video, así que es la palanca más barata para el lote.
PRESET = "veryfast"

# El personaje se dibuja al doble y se reduce: Pillow no tiene antialiasing en
# sus primitivas, así que sin esto los bordes quedan escalonados.
ESCALA_PJ = 2
ANCHO_PJ, ALTO_PJ = 760, 1000

# Caja donde vive el personaje dentro del frame final (esquina superior izquierda).
PJ_X, PJ_Y = (ANCHO - ANCHO_PJ) // 2, 780

VELOCIDAD_ZOOM = 0.045  # fracción de acercamiento durante todo el clip
SILENCIO = 0.08  # por debajo de esto la boca queda cerrada (ruido de fondo)
GANANCIA_BOCA = 0.62


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------

def envolvente_audio(ruta: Path, fps: int = FPS) -> list[float]:
    """Devuelve la energía del audio por frame, normalizada de 0 a 1.

    Se decodifica a PCM crudo con ffmpeg porque esta máquina no tiene ffprobe
    ni una librería de audio instalada, y leer las muestras es exacto: estimar
    la boca desde un temporizador inventado se desincroniza a los dos segundos.
    """
    resultado = subprocess.run(
        [common.FFMPEG, "-i", str(ruta), "-ac", "1", "-ar", "8000",
         "-f", "s16le", "-"],
        capture_output=True, check=True,
    )
    muestras = array.array("h")
    muestras.frombytes(resultado.stdout)

    por_frame = 8000 // fps
    if por_frame <= 0:
        raise ValueError("fps demasiado alto para la frecuencia de muestreo")

    energias: list[float] = []
    for inicio in range(0, len(muestras), por_frame):
        trozo = muestras[inicio:inicio + por_frame]
        if not trozo:
            break
        suma = sum(int(m) * int(m) for m in trozo)
        energias.append(math.sqrt(suma / len(trozo)))

    if not energias:
        return []

    # Normalizar por un percentil alto y no por el máximo: un golpe de voz
    # aislado dejaría el resto del audio con la boca casi cerrada.
    ordenadas = sorted(energias)
    referencia = ordenadas[int(len(ordenadas) * 0.95)] or ordenadas[-1] or 1.0
    return [min(1.0, e / referencia) for e in energias]


def suavizar(energias: list[float]) -> list[float]:
    """Aplica ataque rápido y caída lenta, como hace una boca real.

    Sin suavizado la boca vibra frame a frame y el resultado parece ruido.
    """
    suave: list[float] = []
    previo = 0.0
    for e in energias:
        factor = 0.55 if e > previo else 0.82
        previo = previo + (e - previo) * (1 - factor)
        suave.append(previo)
    return suave


def aperturas_boca(energias: list[float]) -> list[float]:
    """Convierte energía en apertura de boca, recortando el ruido de fondo."""
    salida = []
    for e in suavizar(energias):
        valor = (e - SILENCIO) / GANANCIA_BOCA
        salida.append(max(0.0, min(1.0, valor)))
    return salida


# --------------------------------------------------------------------------
# Fondo
# --------------------------------------------------------------------------

def _degradado(ancho: int, alto: int, arriba: tuple, abajo: tuple) -> Image.Image:
    """Pinta un degradado vertical interpolando color por línea."""
    img = Image.new("RGB", (1, alto))
    px = img.load()
    for y in range(alto):
        t = y / max(1, alto - 1)
        px[0, y] = tuple(int(arriba[i] + (abajo[i] - arriba[i]) * t) for i in range(3))
    return img.resize((ancho, alto), Image.Resampling.BILINEAR)


def fondo_procedural(ancho: int = ANCHO, alto: int = ALTO) -> Image.Image:
    """Dibuja un paraíso de storybook: cielo, sol, montañas, colinas y lago.

    Existe para que el pipeline corra sin depender de una imagen externa, y
    como respaldo cuando la imagen IA no está disponible.
    """
    img = _degradado(ancho, alto, (255, 196, 140), (255, 239, 205)).convert("RGBA")
    capa = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)

    # Sol y sus rayos
    cx, cy, r = int(ancho * 0.70), int(alto * 0.20), int(ancho * 0.13)
    for i in range(12, 0, -1):
        radio = r + i * (r * 0.10)
        d.ellipse([cx - radio, cy - radio, cx + radio, cy + radio],
                  fill=(255, 236, 170, 12))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 250, 214, 235))
    capa = capa.filter(ImageFilter.GaussianBlur(14))

    # Montañas lejanas
    d = ImageDraw.Draw(capa)
    d.polygon([(0, int(alto * 0.50)), (int(ancho * 0.22), int(alto * 0.34)),
               (int(ancho * 0.46), int(alto * 0.50))], fill=(178, 196, 214, 220))
    d.polygon([(int(ancho * 0.30), int(alto * 0.52)), (int(ancho * 0.58), int(alto * 0.36)),
               (int(ancho * 0.88), int(alto * 0.52))], fill=(160, 182, 206, 220))
    capa = capa.filter(ImageFilter.GaussianBlur(3))

    # Colinas superpuestas
    d = ImageDraw.Draw(capa)
    d.ellipse([-int(ancho * 0.5), int(alto * 0.47), int(ancho * 0.75), int(alto * 0.90)],
              fill=(126, 196, 118, 255))
    d.ellipse([int(ancho * 0.28), int(alto * 0.50), int(ancho * 1.5), int(alto * 0.95)],
              fill=(150, 214, 132, 255))
    d.ellipse([-int(ancho * 0.3), int(alto * 0.62), int(ancho * 0.95), int(alto * 1.3)],
              fill=(112, 186, 104, 255))

    # Lago
    d.ellipse([int(ancho * 0.52), int(alto * 0.66), int(ancho * 1.25), int(alto * 0.95)],
              fill=(126, 210, 222, 255))
    d.ellipse([int(ancho * 0.62), int(alto * 0.69), int(ancho * 1.1), int(alto * 0.87)],
              fill=(170, 232, 240, 235))

    # Árboles al fondo
    rnd = random.Random(7)
    for _ in range(18):
        x = rnd.randint(0, ancho)
        y = rnd.randint(int(alto * 0.48), int(alto * 0.66))
        tronco = rnd.randint(10, 18)
        alto_copa = rnd.randint(40, 80)
        d.rectangle([x - tronco // 3, y, x + tronco // 3, y + alto_copa // 2],
                    fill=(120, 92, 60, 255))
        d.ellipse([x - tronco, y - alto_copa, x + tronco, y + alto_copa // 2],
                  fill=(88, 166, 84, 255))

    capa = capa.filter(ImageFilter.GaussianBlur(1))
    return Image.alpha_composite(img, capa)


def cargar_fondo(ruta: Path | None, escenario: str = "paraiso") -> Image.Image:
    """Prepara el fondo al tamaño del frame, recortando sin deformar.

    Se dibuja un poco más grande que el frame para poder hacer zoom sin que
    aparezcan bordes vacíos.

    `escenario` solo se usa cuando no hay imagen (`ruta is None`): el paraíso lo
    dibuja este módulo y el resto, `scripts.escenarios`. La importación es
    diferida porque `escenarios` importa `_degradado` de aquí; hacerlo arriba
    sería un ciclo.
    """
    if ruta is None:
        if escenario == "paraiso":
            base = fondo_procedural()
        else:
            from scripts.util import escenarios
            base = escenarios.dibujar(escenario)
    else:
        base = Image.open(ruta).convert("RGBA")

    factor = max(ANCHO / base.width, ALTO / base.height) * 1.12
    ancho = int(base.width * factor)
    alto = int(base.height * factor)
    return base.resize((ancho, alto), Image.Resampling.LANCZOS)


# --------------------------------------------------------------------------
# Personaje
# --------------------------------------------------------------------------

def _color(hexadecimal: str) -> tuple[int, int, int, int]:
    hexadecimal = hexadecimal.lstrip("#")
    return (int(hexadecimal[0:2], 16), int(hexadecimal[2:4], 16),
            int(hexadecimal[4:6], 16), 255)


def dibujar_personaje(apertura: float, parpadeo: float, brazo: float,
                      bob: float, halo: float) -> Image.Image:
    """Dibuja al jesucitos en una capa RGBA del tamaño de PJ.

    Cada parámetro mueve una capa independiente:
    - apertura: 0 boca cerrada, 1 boca muy abierta
    - parpadeo: 0 ojos abiertos, 1 ojos cerrados
    - brazo: inclinación del brazo que gesticula, de -1 a 1
    - bob: desplazamiento vertical de todo el cuerpo en px
    - halo: 0 glow tenue, 1 glow intenso
    """
    e = ESCALA_PJ
    img = Image.new("RGBA", (ANCHO_PJ * e, ALTO_PJ * e), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx = (ANCHO_PJ // 2) * e
    dy = int(bob * e)

    piel, pelo = _color("#F2C9A0"), _color("#8B5A2B")
    tunica, tunica_sombra = _color("#FFFDF6"), _color("#E4DCC8")
    faja, detalle = _color("#C0392B"), _color("#D4AF37")

    # Halo con glow. Va en su propia capa porque ImageDraw con fill RGBA
    # *reemplaza* el píxel en vez de mezclarlo: dibujar los círculos de glow
    # directamente sobre el personaje deja un disco de borde duro en lugar de
    # un resplandor que acumula.
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    hx, hy, hr = cx, (150 + int(bob)) * e, 96 * e
    for i in range(3):
        radio = hr + (i + 1) * 26 * e
        gd.ellipse([hx - radio, hy - radio, hx + radio, hy + radio],
                   fill=(255, 226, 120, 70))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(22 * e)))
    d = ImageDraw.Draw(img)
    anillo = hr + int(6 * e)
    d.arc([hx - anillo, hy - anillo, hx + anillo, hy + anillo],
          start=0, end=360, fill=(255, 214, 74, 255), width=int(18 * e))

    # Mangas. Se dibujan ANTES de la túnica para que la túnica tape su unión
    # con el hombro: si nacen visibles sobre el borde del vestido, el brazo
    # parece un palo flotando junto al cuerpo en vez de salir de debajo.
    hombro_izq = (cx - 100 * e, (525 + int(bob)) * e)
    # El abanico va de "brazo arriba" (-140°) a "brazo al costado" (-105°).
    # Con ángulos cercanos a -90° la mano terminaba dentro del círculo de la
    # cabeza y el brazo desaparecía del todo.
    angulo = math.radians(-140 + 35 * (brazo + 1) / 2)
    largo = 300 * e
    mano = (hombro_izq[0] + math.cos(angulo) * largo,
            hombro_izq[1] + math.sin(angulo) * largo)
    d.line([hombro_izq, mano], fill=tunica_sombra, width=int(58 * e),
           joint="curve")

    hombro_der = (cx + 100 * e, (525 + int(bob)) * e)
    # La mano derecha va FUERA de la silueta de la túnica: a y=800 la túnica
    # mide ±190, así que a ±150 la mano caía encima del vestido y parecía
    # un botón pegado.
    mano_der = (cx + 245 * e, (800 + int(bob)) * e)
    d.line([hombro_der, mano_der], fill=tunica_sombra, width=int(58 * e))

    # Túnica
    d.polygon([cx - 128 * e, 470 * e, cx + 128 * e, 470 * e,
               cx + 210 * e, 940 * e, cx - 210 * e, 940 * e], fill=tunica)
    d.polygon([cx - 128 * e, 470 * e, cx - 40 * e, 470 * e,
               cx - 70 * e, 940 * e, cx - 210 * e, 940 * e], fill=tunica_sombra)

    # Manos, ya por encima de la túnica
    d.ellipse([mano[0] - 34 * e, mano[1] - 34 * e,
               mano[0] + 34 * e, mano[1] + 34 * e], fill=piel)
    d.ellipse([mano_der[0] - 32 * e, mano_der[1] - 32 * e,
               mano_der[0] + 32 * e, mano_der[1] + 32 * e], fill=piel)

    # Faja y sus pliegues
    d.polygon([cx - 132 * e, 560 * e, cx + 132 * e, 560 * e,
               cx + 140 * e, 615 * e, cx - 140 * e, 615 * e], fill=faja)
    d.line([cx - 10 * e, 560 * e, cx - 6 * e, 615 * e], fill=detalle,
           width=int(6 * e))

    # Cabeza
    hy_cabeza = 340 + int(bob)
    r_cabeza = 150
    d.ellipse([cx - r_cabeza * e, (hy_cabeza - r_cabeza) * e,
               cx + r_cabeza * e, (hy_cabeza + r_cabeza) * e], fill=piel)

    # Pelo
    oscuro = _color("#6B4524")
    d.chord([cx - (r_cabeza + 6) * e, (hy_cabeza - r_cabeza - 8) * e,
             cx + (r_cabeza + 6) * e, (hy_cabeza + 40) * e],
            start=180, end=360, fill=pelo)
    # Mechones que enmarcan la cara. Van pegados al borde del pelo y no como
    # elipses sueltas sobre la mejilla, que parecían auriculares.
    d.ellipse([cx - 148 * e, (hy_cabeza - 104) * e,
               cx - 96 * e, (hy_cabeza + 30) * e], fill=oscuro)
    d.ellipse([cx + 96 * e, (hy_cabeza - 104) * e,
               cx + 148 * e, (hy_cabeza + 30) * e], fill=oscuro)

    # Barba: una elipse maciza que cuelga de la mandíbula. Antes llevaba otra
    # elipse de piel encima para "ahuecarla", y el resultado era un anillo
    # marrón alrededor de la boca. La boca se dibuja después, así que se ve
    # igual de bien y la barba deja de parecer una dona.
    # Arranca por debajo del bigote (~+104), no a la altura de la nariz.
    d.ellipse([cx - 122 * e, (hy_cabeza + 116) * e,
               cx + 122 * e, (hy_cabeza + 258) * e], fill=pelo)
    # Bigote
    d.ellipse([cx - 58 * e, (hy_cabeza + 74) * e,
               cx - 4 * e, (hy_cabeza + 104) * e], fill=pelo)
    d.ellipse([cx + 4 * e, (hy_cabeza + 74) * e,
               cx + 58 * e, (hy_cabeza + 104) * e], fill=pelo)

    # Ojos
    ojo_por = 46 * (1 - 0.92 * parpadeo)
    for lado in (-1, 1):
        ox = cx + lado * 52 * e
        d.ellipse([ox - 26 * e, (hy_cabeza - 26 - ojo_por) * e,
                   ox + 26 * e, (hy_cabeza - 26 + ojo_por) * e],
                  fill=(255, 255, 255, 255))
        if parpadeo < 0.6:
            d.ellipse([ox - 13 * e, (hy_cabeza - 34) * e,
                       ox + 13 * e, (hy_cabeza - 8) * e], fill=(60, 40, 30, 255))
            d.ellipse([ox - 6 * e, (hy_cabeza - 30) * e,
                       ox + 1 * e, (hy_cabeza - 23) * e], fill=(255, 255, 255, 255))
        # Cejas
        d.arc([ox - 30 * e, (hy_cabeza - 74) * e,
               ox + 30 * e, (hy_cabeza - 44) * e],
              start=190, end=350, fill=pelo, width=int(7 * e))

    # Nariz
    d.ellipse([cx - 12 * e, (hy_cabeza + 12) * e,
               cx + 12 * e, (hy_cabeza + 40) * e], fill=(222, 172, 136, 255))

    # Boca: alto proporcional a la apertura del audio
    alto_boca = 8 + 62 * apertura
    ancho_boca = 42 + 16 * apertura
    by = hy_cabeza + 116
    d.ellipse([(cx / e - ancho_boca) * e, (by - alto_boca / 2) * e,
               (cx / e + ancho_boca) * e, (by + alto_boca / 2) * e],
              fill=(92, 30, 34, 255))
    if apertura > 0.35:
        # Lengua y dientes solo cuando la boca está claramente abierta;
        # dibujarlos cerrada la haría parecer una mancha.
        d.ellipse([(cx / e - ancho_boca * 0.6) * e, (by + alto_boca * 0.02) * e,
                   (cx / e + ancho_boca * 0.6) * e, (by + alto_boca * 0.5) * e],
                  fill=(214, 96, 106, 255))
        d.rectangle([(cx / e - ancho_boca * 0.7) * e, (by - alto_boca * 0.5) * e,
                     (cx / e + ancho_boca * 0.7) * e, (by - alto_boca * 0.30) * e],
                    fill=(255, 255, 250, 255))

    # Sandalias
    for lado in (-1, 1):
        fx = cx / e + lado * 78
        d.rounded_rectangle([(fx - 62) * e, 930 * e, (fx + 62) * e, 972 * e],
                            radius=22 * e, fill=(146, 104, 62, 255))

    return img.resize((ANCHO_PJ, ALTO_PJ), Image.Resampling.LANCZOS)


# --------------------------------------------------------------------------
# Composición
# --------------------------------------------------------------------------

def parpadeos(n_frames: int, fps: int = FPS) -> list[float]:
    """Genera parpadeos en instantes irregulares.

    Un parpadeo con intervalo fijo se percibe como un tic mecánico.
    """
    rnd = random.Random(21)
    salida = [0.0] * n_frames
    siguiente = rnd.uniform(1.2, 2.0)
    while siguiente < n_frames / fps:
        inicio = int(siguiente * fps)
        for k, valor in enumerate((1.0, 0.75, 0.3, 0.0)):
            if 0 <= inicio + k < n_frames:
                salida[inicio + k] = valor
        siguiente += rnd.uniform(2.4, 4.2)
    return salida


# Color de la sombra de contacto según el escenario. Una sombra verde sobre
# arena o asfalto delataba que el personaje venía de otro fondo; con el tono de
# cada escena se lee como parte de la imagen.
SOMBRA_POR_ESCENARIO = {
    "paraiso": (20, 60, 40),
    "bosque": (16, 44, 24),
    "ciudad": (24, 26, 40),
    "desierto": (120, 78, 38),
    "playa": (150, 124, 66),
    "montana": (30, 48, 56),
    "biblioteca": (30, 20, 12),
    "gimnasio": (26, 28, 34),
    "noche": (6, 10, 22),
    "abstracto": (14, 16, 30),
}


def componer_frame(fondo: Image.Image, personaje: Image.Image, t: float,
                   duracion: float, escenario: str = "paraiso") -> bytes:
    """Recorta el fondo con zoom y pega al personaje. Devuelve RGB crudo."""
    # Zoom lento: el fondo nunca queda estático, pero tampoco marea.
    progreso = min(1.0, t / max(0.001, duracion))
    zoom = 1.0 + VELOCIDAD_ZOOM * progreso
    ancho_v = int(ANCHO / zoom)
    alto_v = int(ALTO / zoom)
    # Deriva lateral suave para dar profundidad
    deriva = int(math.sin(t * 0.6) * 12)
    x0 = (fondo.width - ancho_v) // 2 + deriva
    y0 = (fondo.height - alto_v) // 2
    x0 = max(0, min(fondo.width - ancho_v, x0))
    y0 = max(0, min(fondo.height - alto_v, y0))

    lienzo = fondo.crop((x0, y0, x0 + ancho_v, y0 + alto_v)).resize(
        (ANCHO, ALTO), Image.Resampling.BILINEAR).convert("RGBA")

    # Sombra de contacto: sin ella el personaje flota sobre el paisaje.
    tono = SOMBRA_POR_ESCENARIO.get(escenario, SOMBRA_POR_ESCENARIO["paraiso"])
    sombra = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).ellipse(
        [PJ_X + 150, PJ_Y + ALTO_PJ - 70, PJ_X + ANCHO_PJ - 150, PJ_Y + ALTO_PJ + 10],
        fill=tono + (90,))
    lienzo = Image.alpha_composite(lienzo, sombra.filter(ImageFilter.GaussianBlur(18)))
    lienzo.alpha_composite(personaje, (PJ_X, PJ_Y))

    return lienzo.convert("RGB").tobytes()


def dibujar_para(personaje: str, spec: dict | None = None):
    """Devuelve una función `fn(**params)` que dibuja el personaje pedido.

    Existe para que el pipeline elija la figura sin duplicar `animar`: el motor
    de lip-sync, el fondo, el zoom y los subtítulos son los mismos; solo cambia
    quién se dibuja. El `spec` (ficha de diseño que decide el LLM) solo aplica
    al personaje `motivacional`.
    """
    if personaje in ("", "jesucitos"):
        return dibujar_personaje
    if personaje == "motivacional":
        from scripts.util import personaje as modulo
        return lambda **params: modulo.dibujar(spec=spec, **params)
    raise ValueError(
        f"Personaje desconocido: {personaje!r}. Usa 'jesucitos' o 'motivacional'."
    )


def animar(audio: Path, fondo_ruta: Path | None, salida: Path,
           personaje: str = "jesucitos", spec: dict | None = None,
           escenario: str = "paraiso") -> Path:
    """Renderiza el video completo y lo codifica con ffmpeg.

    `escenario` elige el fondo procedural cuando no se pasa una imagen: es el
    segundo eje de variedad entre videos, junto con la ficha del personaje.
    """
    info = common.probe(audio)
    duracion = info["duracion"]
    if not duracion:
        raise ValueError(f"No se pudo leer la duración de {audio}")

    dibujar = dibujar_para(personaje, spec)
    fondo = cargar_fondo(fondo_ruta, escenario)
    aperturas = aperturas_boca(envolvente_audio(audio))
    n_frames = max(1, int(round(duracion * FPS)))
    # Si el audio es más corto que el número de frames esperado, se rellena
    # con boca cerrada en vez de fallar.
    aperturas += [0.0] * (n_frames - len(aperturas))
    parpadeo = parpadeos(n_frames)

    # ffmpeg no puede escribir el mp4 directamente en el volumen (no soporta la
    # reescritura del trailer), así que se renderiza en /tmp y se copia al final.
    destino, copiar = common.renderizar_en_local(salida)
    destino.parent.mkdir(parents=True, exist_ok=True)

    # El log de ffmpeg va a un archivo y no a un pipe: con stderr=PIPE y
    # nadie leyéndolo, ffmpeg llena el buffer del pipe y se queda bloqueado
    # escribiendo mientras nosotros esperamos escribiendo stdin. Ese deadlock
    # deja el mp4 sin átomo `moov` y parece un render eterno.
    with tempfile.TemporaryFile() as log:
        proceso = subprocess.Popen(
            [common.FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{ANCHO}x{ALTO}", "-r", str(FPS), "-i", "-",
             "-i", str(audio),
             "-c:v", "libx264", "-crf", str(CRF), "-preset", PRESET,
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-shortest", str(destino)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
        )
        assert proceso.stdin is not None

        try:
            for i in range(n_frames):
                t = i / FPS
                bob = math.sin(t * 2.6) * 7
                brazo = math.sin(t * 1.9) * 0.85
                halo = 0.55 + 0.45 * math.sin(t * 1.5)
                capa = dibujar(
                    apertura=aperturas[i], parpadeo=parpadeo[i],
                    brazo=brazo, bob=bob, halo=halo,
                )
                proceso.stdin.write(componer_frame(fondo, capa, t, duracion, escenario))
        finally:
            proceso.stdin.close()
            codigo = proceso.wait()

        if codigo != 0:
            log.seek(0)
            error = log.read().decode("utf-8", "replace")
            # Solo las líneas de error: el banner de ffmpeg ocupa 1200 caracteres
            # y las estadísticas de x264 otros cientos, así que un recorte por
            # posición dejaba fuera justo la línea que explicaba el fallo.
            claves = ("rror", "pace", "moov", "Invalid", "failed", "No such",
                      "Permission", "denied", "truncat", "Cannot", "unable")
            causa = [
                l.strip() for l in error.splitlines() if any(c in l for c in claves)
            ]
            raise RuntimeError(
                f"ffmpeg falló ({codigo}):\n" + "\n".join(causa[:20] or [error[-800:]])
            )

    if copiar:
        common.copiar_a_destino(destino, salida)

    return salida


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--fondo", default=None, help="imagen IA del paraíso (opcional)")
    parser.add_argument("--out", default="voz/jesucitos.mp4")
    parser.add_argument("--personaje", default="jesucitos",
                        choices=["jesucitos", "motivacional"])
    from scripts.util.escenarios import TODOS as ESCENARIOS_VALIDOS
    parser.add_argument("--escenario", default="paraiso", choices=list(ESCENARIOS_VALIDOS),
                        help="fondo procedural cuando no se pasa --fondo")
    args = parser.parse_args(argv)
    common.configurar_salida()

    salida = animar(Path(args.audio),
                    Path(args.fondo) if args.fondo else None,
                    Path(args.out),
                    personaje=args.personaje, escenario=args.escenario)
    info = common.probe(salida)
    print(f"✅ {salida} ({info['duracion']:.1f}s, {info['ancho']}x{info['alto']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
