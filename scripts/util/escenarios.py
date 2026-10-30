"""Fondos procedurales alternativos al paraíso, para variar la escena por guion.

`jesucitos.fondo_procedural` dibuja el paraíso de storybook, que era el único
escenario disponible. Aquí se añaden otras escenas con el mismo lenguaje visual
(formas planas, degradado, capas desenfocadas para dar profundidad), de modo que
dos videos con personajes distintos tampoco compartan paisaje.

Igual que el personaje, el escenario lo decide el LLM a partir del guion: un
guion de superación en el gimnasio no debería salir sobre una playa.

No se duplica el paraíso: `dibujar("paraiso")` es un error a propósito, y
`jesucitos.cargar_fondo` sigue siendo quien lo dibuja.
"""

from __future__ import annotations

import random

from PIL import Image, ImageDraw, ImageFilter

from scripts.util.jesucitos import _degradado

ANCHO, ALTO = 1080, 1920

# Nombres que el LLM puede pedir además del paraíso.
ESCENARIOS = (
    "ciudad",
    "desierto",
    "montana",
    "bosque",
    "biblioteca",
    "gimnasio",
    "noche",
    "playa",
    "abstracto",
)

# Todos los escenarios válidos para el LLM: el paraíso es uno más.
TODOS = ("paraiso",) + ESCENARIOS


def _capa(ancho: int, alto: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    capa = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    return capa, ImageDraw.Draw(capa)


def _ciudad(ancho: int, alto: int) -> Image.Image:
    """Skyline al atardecer. Los edificios lejanos van más claros y difusos."""
    img = _degradado(ancho, alto, (58, 66, 120), (255, 158, 122)).convert("RGBA")
    rnd = random.Random(11)

    for profundidad, (color, base) in enumerate(
        [((92, 96, 138, 235), 0.62), ((62, 66, 104, 245), 0.68)]
    ):
        capa, d = _capa(ancho, alto)
        x = -40
        while x < ancho:
            ancho_edificio = rnd.randint(70, 150)
            alto_edificio = rnd.randint(int(alto * 0.16), int(alto * 0.34))
            techo = int(alto * base) - alto_edificio
            d.rectangle([x, techo, x + ancho_edificio, alto], fill=color)
            # Ventanas encendidas: sin ellas el skyline es una masa negra.
            for fy in range(techo + 22, alto, 46):
                for fx in range(x + 14, x + ancho_edificio - 14, 34):
                    if rnd.random() < 0.42:
                        d.rectangle([fx, fy, fx + 14, fy + 20],
                                    fill=(255, 219, 150, 210))
            x += ancho_edificio + rnd.randint(6, 26)
        if profundidad == 0:
            capa = capa.filter(ImageFilter.GaussianBlur(3))
        img = Image.alpha_composite(img, capa)
    return img


def _desierto(ancho: int, alto: int) -> Image.Image:
    img = _degradado(ancho, alto, (247, 170, 96), (255, 233, 190)).convert("RGBA")
    capa, d = _capa(ancho, alto)
    # Sol bajo y grande
    cx, cy, r = int(ancho * 0.68), int(alto * 0.26), int(ancho * 0.11)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 246, 206, 240))
    img = Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(10)))

    # Dunas: elipses anchas recortadas por el borde inferior del frame.
    for i, (y, color) in enumerate(
        [(0.56, (226, 178, 116)), (0.66, (216, 160, 96)), (0.78, (198, 138, 78))]
    ):
        capa, d = _capa(ancho, alto)
        d.ellipse([-int(ancho * 0.35), int(alto * y), int(ancho * 1.35),
                   int(alto * (y + 0.55))], fill=color + (255,))
        img = Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(2 + i)))
    return img


def _montana(ancho: int, alto: int) -> Image.Image:
    img = _degradado(ancho, alto, (96, 128, 176), (214, 232, 245)).convert("RGBA")
    # Picos con nieve
    for puntos, color in [
        ([(0, 0.60), (0.28, 0.30), (0.55, 0.60)], (118, 140, 172)),
        ([(0.34, 0.62), (0.62, 0.26), (0.92, 0.62)], (100, 124, 158)),
        ([(0.70, 0.64), (0.88, 0.40), (1.05, 0.64)], (132, 152, 182)),
    ]:
        capa, d = _capa(ancho, alto)
        d.polygon([(int(x * ancho), int(y * alto)) for x, y in puntos],
                  fill=color + (245,))
        cima_x, cima_y = puntos[1]
        # Casquete de nieve: un triángulo pequeño en la cima.
        d.polygon([(int((cima_x - 0.05) * ancho), int((cima_y + 0.06) * alto)),
                   (int(cima_x * ancho), int(cima_y * alto)),
                   (int((cima_x + 0.05) * ancho), int((cima_y + 0.06) * alto))],
                  fill=(250, 252, 255, 255))
        img = Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(2)))

    capa, d = _capa(ancho, alto)
    d.ellipse([-int(ancho * 0.6), int(alto * 0.58), int(ancho * 0.9), int(alto * 1.2)],
              fill=(92, 138, 104, 255))
    d.ellipse([int(ancho * 0.35), int(alto * 0.62), int(ancho * 1.5), int(alto * 1.25)],
              fill=(78, 122, 92, 255))
    return Image.alpha_composite(img, capa)


def _bosque(ancho: int, alto: int) -> Image.Image:
    img = _degradado(ancho, alto, (168, 206, 150), (238, 240, 202)).convert("RGBA")
    capa, d = _capa(ancho, alto)
    rnd = random.Random(5)
    # Rayos de luz: bandas inclinadas y difusas.
    for i in range(6):
        x0 = int(ancho * (0.08 + i * 0.16))
        ancho_rayo = rnd.randint(30, 70)
        d.polygon([(x0, 0), (x0 + ancho_rayo, 0),
                   (x0 + ancho_rayo + 220, alto), (x0 + 220, alto)],
                  fill=(255, 255, 225, 26))
    img = Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(18)))

    # Árboles: tronco y copa. Sin copa, un tronco alto sobre un degradado verde
    # se lee como una barra, no como un bosque.
    for profundidad, (tronco_c, copa_c, borroso) in enumerate(
        [((84, 108, 74, 200), (96, 142, 86, 190), 5),
         ((58, 78, 52, 240), (54, 104, 58, 245), 0)]
    ):
        capa, d = _capa(ancho, alto)
        rnd2 = random.Random(21 + profundidad)
        for _ in range(7):
            x = rnd2.randint(-40, ancho)
            grosor = rnd2.randint(22, 40) + profundidad * 8
            pie = int(alto * (0.86 + 0.04 * profundidad))
            d.rectangle([x, int(alto * 0.34), x + grosor, pie], fill=tronco_c)
            # Copa: dos elipses solapadas, para que no sea un círculo perfecto.
            copa_r = grosor * rnd2.uniform(2.4, 3.6)
            cy = int(alto * rnd2.uniform(0.16, 0.30))
            d.ellipse([x + grosor / 2 - copa_r, cy - copa_r,
                       x + grosor / 2 + copa_r, cy + copa_r], fill=copa_c)
            d.ellipse([x + grosor / 2 - copa_r * 0.8, cy - copa_r * 1.4,
                       x + grosor / 2 + copa_r * 0.8, cy + copa_r * 0.6], fill=copa_c)
        img = Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(borroso)))
    return img


def _biblioteca(ancho: int, alto: int) -> Image.Image:
    img = _degradado(ancho, alto, (92, 62, 42), (58, 40, 28)).convert("RGBA")
    capa, d = _capa(ancho, alto)
    rnd = random.Random(3)
    # Estanterías con lomos de colores apagados.
    for estante_y in range(int(alto * 0.10), int(alto * 0.78), 190):
        d.rectangle([0, estante_y + 150, ancho, estante_y + 166],
                    fill=(46, 30, 20, 255))
        x = 10
        while x < ancho - 10:
            grosor = rnd.randint(16, 34)
            alto_lomo = rnd.randint(96, 148)
            color = rnd.choice([(122, 44, 40), (40, 62, 92), (34, 82, 66),
                                (126, 96, 46), (96, 52, 88), (60, 60, 66)])
            d.rectangle([x, estante_y + 150 - alto_lomo, x + grosor,
                         estante_y + 150], fill=color + (255,))
            x += grosor + rnd.randint(2, 8)
    img = Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(4)))

    # Luz cálida de lámpara sobre el centro.
    capa, d = _capa(ancho, alto)
    d.ellipse([int(ancho * 0.10), int(alto * 0.02), int(ancho * 0.90), int(alto * 0.62)],
              fill=(255, 216, 150, 44))
    return Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(40)))


def _gimnasio(ancho: int, alto: int) -> Image.Image:
    img = _degradado(ancho, alto, (74, 82, 96), (132, 142, 156)).convert("RGBA")
    capa, d = _capa(ancho, alto)
    # Ventanal alto con luz fría
    d.rectangle([int(ancho * 0.06), int(alto * 0.06),
                 int(ancho * 0.94), int(alto * 0.34)],
                fill=(196, 222, 236, 120))
    img = Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(12)))

    capa, d = _capa(ancho, alto)
    ACERO = (46, 50, 60, 255)

    # Suelo de goma con zócalo. Va primero: todo lo demás apoya encima.
    d.rectangle([0, int(alto * 0.74), ancho, alto], fill=(58, 60, 68, 255))
    d.rectangle([0, int(alto * 0.74), ancho, int(alto * 0.765)], fill=(38, 40, 48, 255))

    # Rack de sentadillas, centrado: dos postes verticales, la barra que los
    # une y dos discos a los lados. Es la silueta que se reconoce como gimnasio
    # sin necesidad de detalle; una mancuerna suelta se leía como unas gafas.
    x0, x1 = int(ancho * 0.30), int(ancho * 0.70)
    for px in (x0, x1):
        d.rectangle([px, int(alto * 0.20), px + 30, int(alto * 0.76)], fill=ACERO)
        # Pies de la base
        d.rectangle([px - 26, int(alto * 0.73), px + 56, int(alto * 0.76)], fill=ACERO)
    barra_y = int(alto * 0.52)
    d.rectangle([x0 - 150, barra_y, x1 + 180, barra_y + 22], fill=ACERO)
    # Discos sólidos con el canto iluminado. Dos intentos previos fallaron por
    # la misma razón: una elipse interior de otro tono se lee como agujero
    # (anilla) y, si cruza el poste, como aspa de tijera. Sólido + canto, y
    # separados de los postes, sí se leen como pesas.
    for cx in (x0 - 116, x1 + 146):
        d.ellipse([cx - 56, barra_y - 66, cx + 56, barra_y + 88],
                  fill=ACERO, outline=(96, 104, 122, 255), width=7)

    # Banco plano delante del rack
    d.rectangle([int(ancho * 0.34), int(alto * 0.70),
                 int(ancho * 0.66), int(alto * 0.725)], fill=(40, 44, 54, 255))
    d.rectangle([int(ancho * 0.37), int(alto * 0.725),
                 int(ancho * 0.63), int(alto * 0.75)], fill=(30, 34, 42, 255))

    return Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(2)))


def _noche(ancho: int, alto: int) -> Image.Image:
    img = _degradado(ancho, alto, (12, 18, 44), (52, 62, 104)).convert("RGBA")
    capa, d = _capa(ancho, alto)
    rnd = random.Random(99)
    for _ in range(160):
        x, y = rnd.randint(0, ancho), rnd.randint(0, int(alto * 0.72))
        r = rnd.choice([1, 1, 1, 2, 2, 3])
        d.ellipse([x - r, y - r, x + r, y + r],
                  fill=(255, 255, 245, rnd.randint(120, 255)))
    # Luna con halo
    mx, my, mr = int(ancho * 0.24), int(alto * 0.16), int(ancho * 0.075)
    for i in range(6, 0, -1):
        radio = mr + i * 22
        d.ellipse([mx - radio, my - radio, mx + radio, my + radio],
                  fill=(200, 218, 255, 16))
    d.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=(246, 246, 230, 255))
    img = Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(1)))

    # Perfil de colinas nocturnas
    capa, d = _capa(ancho, alto)
    d.ellipse([-int(ancho * 0.5), int(alto * 0.64), int(ancho * 0.95), int(alto * 1.3)],
              fill=(20, 30, 48, 255))
    d.ellipse([int(ancho * 0.40), int(alto * 0.68), int(ancho * 1.5), int(alto * 1.35)],
              fill=(16, 24, 40, 255))
    return Image.alpha_composite(img, capa)


def _playa(ancho: int, alto: int) -> Image.Image:
    img = _degradado(ancho, alto, (128, 206, 236), (240, 250, 252)).convert("RGBA")
    capa, d = _capa(ancho, alto)
    sx, sy, sr = int(ancho * 0.76), int(alto * 0.18), int(ancho * 0.09)
    for i in range(8, 0, -1):
        radio = sr + i * 26
        d.ellipse([sx - radio, sy - radio, sx + radio, sy + radio],
                  fill=(255, 246, 200, 14))
    d.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=(255, 250, 214, 240))
    img = Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(20)))

    capa, d = _capa(ancho, alto)
    # Mar
    d.rectangle([0, int(alto * 0.52), ancho, int(alto * 0.74)],
                fill=(58, 158, 186, 255))
    # Espuma: bandas horizontales irregulares
    rnd = random.Random(31)
    for y in range(int(alto * 0.54), int(alto * 0.74), 26):
        d.line([(0, y), (ancho, y + rnd.randint(-8, 8))],
               fill=(226, 246, 250, 120), width=rnd.randint(3, 7))
    # Arena
    d.ellipse([-int(ancho * 0.2), int(alto * 0.70), int(ancho * 1.2), int(alto * 1.1)],
              fill=(240, 224, 186, 255))
    return Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(2)))


def _abstracto(ancho: int, alto: int) -> Image.Image:
    """Bokeh de color: sirve para guiones sin lugar concreto."""
    img = _degradado(ancho, alto, (44, 52, 88), (18, 22, 38)).convert("RGBA")
    rnd = random.Random(17)
    paleta = [(120, 180, 255), (255, 140, 170), (140, 240, 200),
              (255, 206, 120), (170, 150, 255)]
    capa, d = _capa(ancho, alto)
    for _ in range(26):
        x, y = rnd.randint(0, ancho), rnd.randint(0, alto)
        r = rnd.randint(70, 230)
        # Alfa alto y desenfoque moderado: con 26 px de radio y alfa ~30 los
        # círculos se fundían con el degradado y el fondo quedaba liso.
        d.ellipse([x - r, y - r, x + r, y + r],
                  fill=rnd.choice(paleta) + (rnd.randint(90, 150),))
    return Image.alpha_composite(img, capa.filter(ImageFilter.GaussianBlur(12)))


_DIBUJANTES = {
    "ciudad": _ciudad,
    "desierto": _desierto,
    "montana": _montana,
    "bosque": _bosque,
    "biblioteca": _biblioteca,
    "gimnasio": _gimnasio,
    "noche": _noche,
    "playa": _playa,
    "abstracto": _abstracto,
}


def normalizar(nombre: str | None) -> str:
    """Devuelve un escenario válido; cualquier cosa rara cae a "paraiso"."""
    limpio = (nombre or "").strip().lower()
    return limpio if limpio in TODOS else "paraiso"


def dibujar(nombre: str, ancho: int = ANCHO, alto: int = ALTO) -> Image.Image:
    """Dibuja el escenario pedido. "paraiso" lo dibuja `jesucitos`, no esto."""
    nombre = normalizar(nombre)
    if nombre == "paraiso":
        raise ValueError("El paraíso lo dibuja jesucitos.fondo_procedural, no este módulo")
    return _DIBUJANTES[nombre](ancho, alto)


__all__ = ["ESCENARIOS", "TODOS", "dibujar", "normalizar"]
