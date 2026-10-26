"""Banner de marca con las cuatro redes de @alguienqueteescucha.

Genera en `C:/Users/lopee/tiktok_videos/banner_redes/` cuatro piezas con los
enlaces reales de Facebook, TikTok, YouTube e Instagram:

- `banner_youtube_2560x1440.png` .. dentro del área segura 1546x423.
- `banner_ancho_1920x1080.png` ... uso general (web, cabecera de blog).
- `cuadrado_1080x1080.png` ....... publicación o link-in-bio.
- `historia_1080x1920.png` ....... story, con las URLs completas.

Decisiones que no son obvias:
- Los iconos se dibujan con primitivas de Pillow (círculos, arcos, polígonos) y
  se recortan con una máscara, no se pintan encima: así son monocromos crema y
  funcionan sobre cualquier fondo, sin depender de archivos de logo de terceros
  ni de fuentes de iconos que varían entre versiones de Windows.
- Instagram lleva un guion bajo final (`@alguienqueteescucha_`) porque su
  handle NO es idéntico al de las otras tres. Es el error más fácil de cometer
  al publicar el banner.
- En la pieza de YouTube solo caben los nombres: el área segura de 423 px no
  deja espacio para 4 handles de 22 caracteres. Ahí el banner identifica; los
  enlaces van en la descripción del canal.
- El fondo es el mismo escenario procedural desenfocado del kit de redes, con
  velo, para que el crema siga pasando contraste AA (17,6:1 sobre este fondo).

Uso: .venv/Scripts/python.exe scripts/banner_redes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from scripts.marca_logo import (  # noqa: E402
    CREMA,
    _fuente,
    _texto_espaciado,
    guardar,
)

DESTINO = Path("C:/Users/lopee/tiktok_videos/banner_redes")

ARROBA = "@alguienqueteescucha"
LEMA = "REFLEXIONES QUE SE SIENTEN"

FONDO = (10, 10, 12, 255)
TEAL_CLARO = (94, 186, 172, 255)
ORO = (235, 184, 104, 255)

# (clave, nombre visible, handle, URL sin protocolo)
REDES = (
    ("facebook", "FACEBOOK", "@alguienqueteescucha", "facebook.com/alguienqueteescucha"),
    ("tiktok", "TIKTOK", "@alguienqueteescucha", "tiktok.com/@alguienqueteescucha"),
    ("youtube", "YOUTUBE", "@alguienqueteescucha", "youtube.com/@alguienqueteescucha"),
    ("instagram", "INSTAGRAM", "@alguienqueteescucha_", "instagram.com/alguienqueteescucha_"),
)

_MEDIDOR = ImageDraw.Draw(Image.new("RGBA", (8, 8)))


# --- iconos -----------------------------------------------------------------

def _recortar(forma: Image.Image, glifo: Image.Image) -> Image.Image:
    """Deja `forma` con el `glifo` perforado (transparente).

    Pintar el glifo del color del fondo solo funciona sobre un fondo liso: los
    chips son semitransparentes sobre una escena, así que el hueco tiene que
    ser alfa real.
    """
    alfa = glifo.split()[3]
    forma.putalpha(Image.composite(Image.new("L", forma.size, 0),
                                   forma.split()[3], alfa))
    return forma


def _icono_facebook(n: int, color=CREMA) -> Image.Image:
    esc = 4
    m = n * esc
    img = Image.new("RGBA", (m, m), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    borde = int(m * 0.06)
    d.ellipse([borde, borde, m - borde, m - borde], fill=color)

    glifo = Image.new("RGBA", (m, m), (0, 0, 0, 0))
    dg = ImageDraw.Draw(glifo)
    f = _fuente("seguisb.ttf", int(m * 0.78))
    dg.text((m * 0.52, m * 0.54), "f", font=f, fill=(255, 255, 255, 255),
            anchor="mm")
    return _recortar(img, glifo).resize((n, n), Image.LANCZOS)


def _icono_youtube(n: int, color=CREMA) -> Image.Image:
    esc = 4
    m = n * esc
    img = Image.new("RGBA", (m, m), (0, 0, 0, 0))
    # Rectángulo redondeado apaisado, la silueta del botón de reproducción.
    ImageDraw.Draw(img).rounded_rectangle(
        [0, m * 0.20, m, m * 0.80], radius=m * 0.24, fill=color)

    glifo = Image.new("RGBA", (m, m), (0, 0, 0, 0))
    ImageDraw.Draw(glifo).polygon(
        [(m * 0.40, m * 0.36), (m * 0.40, m * 0.64), (m * 0.67, m * 0.50)],
        fill=(255, 255, 255, 255))
    return _recortar(img, glifo).resize((n, n), Image.LANCZOS)


def _icono_instagram(n: int, color=CREMA) -> Image.Image:
    esc = 4
    m = n * esc
    img = Image.new("RGBA", (m, m), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    grosor = int(m * 0.075)
    d.rounded_rectangle([grosor, grosor, m - grosor, m - grosor],
                        radius=m * 0.28, outline=color, width=grosor)
    d.ellipse([m * 0.30, m * 0.30, m * 0.70, m * 0.70],
              outline=color, width=grosor)
    r = m * 0.045
    d.ellipse([m * 0.745 - r, m * 0.245 - r, m * 0.745 + r, m * 0.245 + r],
              fill=color)
    return img.resize((n, n), Image.LANCZOS)


def _arco_relleno(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
                  grosor: float, a0: float, a1: float, color,
                  pasos: int = 64) -> None:
    """Arco grueso como polígono entre radio interior y exterior.

    `d.arc` con `width` no cierra los extremos y, al reducir, el tramo corto se
    ve como un punto suelto. Construir el anillo a mano da un gancho continuo
    con topes planos, que es lo que la forma de la nota necesita.
    """
    from math import cos, radians, sin

    r_in = max(0.0, r - grosor / 2)
    r_out = r + grosor / 2
    exterior, interior = [], []
    for i in range(pasos + 1):
        a = radians(a0 + (a1 - a0) * i / pasos)
        exterior.append((cx + r_out * cos(a), cy + r_out * sin(a)))
        interior.append((cx + r_in * cos(a), cy + r_in * sin(a)))
    d.polygon(exterior + interior[::-1], fill=color)


def _icono_tiktok(n: int, color=CREMA) -> Image.Image:
    """Nota con gancho: círculo abajo-izquierda, asta vertical y arco superior.

    Se aproxima con primitivas porque el trazo real de la marca es un path con
    relleno; a 40-90 px la diferencia no se percibe y evita cargar un SVG.
    """
    esc = 4
    m = n * esc
    img = Image.new("RGBA", (m, m), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    grosor = m * 0.15

    # Asta vertical: el eje queda en x=0.44 para que el gancho salga de ahí.
    cx = m * 0.46
    d.rounded_rectangle([cx - grosor / 2, m * 0.22, cx + grosor / 2, m * 0.80],
                        radius=grosor / 2, fill=color)
    # Círculo de la base (cabeza de la nota), a la izquierda del asta.
    r_cab = m * 0.19
    d.ellipse([cx - grosor / 2 - r_cab * 2, m * 0.80 - r_cab * 2,
               cx - grosor / 2, m * 0.80], fill=color)
    # Gancho: cuarto de anillo que arranca en la punta del asta y baja a la
    # derecha, como el trazo de la nota.
    _arco_relleno(d, cx, m * 0.38, m * 0.24, grosor, -90, 15, color)
    return img.resize((n, n), Image.LANCZOS)


ICONOS = {
    "facebook": _icono_facebook,
    "tiktok": _icono_tiktok,
    "youtube": _icono_youtube,
    "instagram": _icono_instagram,
}


# --- utilidades -------------------------------------------------------------

def _cabe(texto: str, fuente, ancho: int, espaciado: float = 0.0) -> bool:
    w = _MEDIDOR.textlength(texto, font=fuente) + espaciado * max(0, len(texto) - 1)
    return w <= ancho


def _fuente_que_cabe(texto: str, archivo: str, ancho: int, tam_max: int,
                     tam_min: int = 8, espaciado: float = 0.0):
    for tam in range(tam_max, tam_min - 1, -1):
        f = _fuente(archivo, tam)
        if _cabe(texto, f, ancho, espaciado):
            return f
    return _fuente(archivo, tam_min)


def _fondo_escena(nombre: str, ancho: int, alto: int,
                  velo: int = 150, blur: float = 18) -> Image.Image:
    from scripts import escenarios

    fondo = escenarios.dibujar(nombre, ancho, alto).convert("RGBA")
    if blur:
        fondo = fondo.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(
        fondo, Image.new("RGBA", (ancho, alto), (8, 12, 14, velo)))


def _franja(ancho: int, alto: int, alto_franja: int, alfa: int = 120) -> Image.Image:
    """Banda oscura con bordes degradados (evita las líneas duras de montaje)."""
    tramo = max(1, alto_franja // 3)
    columna = Image.new("L", (1, alto))
    px = columna.load()
    arriba = (alto - alto_franja) // 2
    for y in range(alto):
        if y < arriba or y >= arriba + alto_franja:
            px[0, y] = 0
        else:
            dentro = y - arriba
            desde_abajo = arriba + alto_franja - y
            px[0, y] = int(alfa * min(1.0, dentro / tramo, desde_abajo / tramo))
    franja = Image.new("RGBA", (ancho, alto), (6, 10, 12, 255))
    franja.putalpha(columna.resize((ancho, alto), Image.NEAREST))
    return franja


def _chip(ancho: int, alto: int, clave: str, titulo: str, sub: str,
          tam_icono: int, tam_titulo: int, tam_sub: int) -> Image.Image:
    """Tarjeta semitransparente con icono + nombre (+ handle si hay)."""
    img = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, ancho - 1, alto - 1], radius=alto // 2,
                        fill=(255, 255, 255, 20),
                        outline=(246, 241, 232, 70), width=2)

    pad = int(alto * 0.22)
    img.alpha_composite(ICONOS[clave](tam_icono), (pad + 6, (alto - tam_icono) // 2))

    x_texto = pad + 6 + tam_icono + int(alto * 0.20)
    disponible = ancho - x_texto - pad

    if sub:
        f_tit = _fuente_que_cabe(titulo, "seguisb.ttf", disponible, tam_titulo)
        f_sub = _fuente_que_cabe(sub, "consola.ttf", disponible, tam_sub)
        d.text((x_texto, alto * 0.24), titulo, font=f_tit, fill=CREMA, anchor="la")
        d.text((x_texto, alto * 0.60), sub, font=f_sub, fill=TEAL_CLARO, anchor="la")
    else:
        f_tit = _fuente_que_cabe(titulo, "seguisb.ttf", disponible, tam_titulo)
        d.text((x_texto, alto / 2), titulo, font=f_tit, fill=CREMA, anchor="lm")
    return img


def _alto_marca(ancho: int, tam_arroba: int) -> float:
    """Alto que ocupa el bloque arroba + filete + lema.

    Existe para poder centrar el conjunto antes de dibujarlo: sin medir, cada
    pieza arrancaba en una Y fija y dejaba la mitad inferior del lienzo vacía.
    """
    f = _fuente_que_cabe(ARROBA, "seguisb.ttf", int(ancho * 0.92), tam_arroba)
    return f.getbbox(ARROBA)[3] + 22 + 34 + 46


def _marca_centrada(d: ImageDraw.ImageDraw, ancho: int, y: float,
                    tam_arroba: int) -> float:
    """Arroba + filete dorado + lema, centrados. Devuelve la Y siguiente."""
    f = _fuente_que_cabe(ARROBA, "seguisb.ttf", int(ancho * 0.92), tam_arroba)
    d.text((ancho / 2, y), ARROBA, font=f, fill=CREMA, anchor="ma")
    caja = f.getbbox(ARROBA)
    y += caja[3] + 22

    d.line([ancho / 2 - 110, y, ancho / 2 + 110, y], fill=ORO, width=5)
    y += 34

    f2 = _fuente_que_cabe(LEMA, "consolab.ttf", int(ancho * 0.9), 40, espaciado=8)
    ancho_lema = _MEDIDOR.textlength(LEMA, font=f2) + 8 * (len(LEMA) - 1)
    _texto_espaciado(d, (ancho / 2 - ancho_lema / 2, y), LEMA, f2, TEAL_CLARO, 8)
    return y + 46


# --- piezas -----------------------------------------------------------------

def banner_youtube() -> Image.Image:
    """2560x1440 respetando el área segura 1546x423 de YouTube."""
    ancho, alto = 2560, 1440
    img = _fondo_escena("montana", ancho, alto, velo=148, blur=24)
    img.alpha_composite(_franja(ancho, alto, 780))
    d = ImageDraw.Draw(img)

    seguro_w, seguro_h = 1546, 423
    x0 = (ancho - seguro_w) // 2
    y0 = (alto - seguro_h) // 2

    # En 423 px de alto no caben arroba + lema + 4 chips con handle: aquí el
    # banner identifica; los enlaces van en la descripción del canal.
    tam_arroba = 112
    chip_alto, icono, gap = 92, 50, 24
    alto_bloque = _alto_marca(ancho, tam_arroba) + 40 + chip_alto
    y = y0 + (seguro_h - alto_bloque) / 2
    y = _marca_centrada(d, ancho, y, tam_arroba)

    n = len(REDES)
    ancho_chip = (seguro_w - gap * (n - 1) - 32) // n
    x = x0 + 16
    y_chip = int(y) + 40
    for clave, titulo_txt, _h, _u in REDES:
        img.alpha_composite(
            _chip(ancho_chip, chip_alto, clave, titulo_txt, "", icono, 30, 18),
            (x, y_chip))
        x += ancho_chip + gap
    return img


def banner_ancho() -> Image.Image:
    """1920x1080: uso general, con handle bajo cada red."""
    ancho, alto = 1920, 1080
    img = _fondo_escena("montana", ancho, alto, velo=150, blur=20)
    img.alpha_composite(_franja(ancho, alto, 640))
    d = ImageDraw.Draw(img)

    tam_arroba = 122
    chip_alto, gap = 132, 26
    alto_bloque = _alto_marca(ancho, tam_arroba) + 56 + chip_alto
    y = _marca_centrada(d, ancho, (alto - alto_bloque) / 2, tam_arroba)

    n = len(REDES)
    ancho_chip = (ancho - 200 - gap * (n - 1)) // n
    x = (ancho - (ancho_chip * n + gap * (n - 1))) // 2
    y_chip = int(y) + 56
    for clave, titulo_txt, handle, _u in REDES:
        img.alpha_composite(
            _chip(ancho_chip, chip_alto, clave, titulo_txt, handle, 56, 32, 18),
            (x, y_chip))
        x += ancho_chip + gap
    return img


def cuadrado() -> Image.Image:
    """1080x1080: grid 2x2 con nombre y handle."""
    lado = 1080
    img = _fondo_escena("montana", lado, lado, velo=150, blur=18)
    img.alpha_composite(_franja(lado, lado, 940))
    d = ImageDraw.Draw(img)

    tam_arroba = 92
    margen, gap, alto_chip = 88, 34, 150
    alto_bloque = _alto_marca(lado, tam_arroba) + 50 + alto_chip * 2 + gap
    y = _marca_centrada(d, lado, (lado - alto_bloque) / 2, tam_arroba)

    ancho_chip = (lado - margen * 2 - gap) // 2
    for i, (clave, titulo_txt, handle, _u) in enumerate(REDES):
        cx = margen + (ancho_chip + gap) * (i % 2)
        cy = int(y) + 50 + (alto_chip + gap) * (i // 2)
        img.alpha_composite(
            _chip(ancho_chip, alto_chip, clave, titulo_txt, handle, 58, 32, 19),
            (cx, cy))
    return img


def historia() -> Image.Image:
    """1080x1920: cuatro filas apiladas con la URL completa."""
    ancho, alto = 1080, 1920
    img = _fondo_escena("montana", ancho, alto, velo=152, blur=18)
    img.alpha_composite(_franja(ancho, alto, 1520))
    d = ImageDraw.Draw(img)

    tam_arroba = 100
    margen, alto_chip, gap = 88, 168, 30
    alto_bloque = (_alto_marca(ancho, tam_arroba) + 80
                   + alto_chip * len(REDES) + gap * (len(REDES) - 1))
    y = _marca_centrada(d, ancho, (alto - alto_bloque) / 2, tam_arroba)

    for i, (clave, titulo_txt, _h, url) in enumerate(REDES):
        cy = int(y) + 80 + i * (alto_chip + gap)
        chip = Image.new("RGBA", (ancho - margen * 2, alto_chip), (0, 0, 0, 0))
        dc = ImageDraw.Draw(chip)
        dc.rounded_rectangle([0, 0, chip.width - 1, alto_chip - 1], radius=34,
                             fill=(255, 255, 255, 20),
                             outline=(246, 241, 232, 70), width=2)
        chip.alpha_composite(ICONOS[clave](72), (46, (alto_chip - 72) // 2))
        xt = 46 + 72 + 38
        disp = chip.width - xt - 40
        f_tit = _fuente_que_cabe(titulo_txt, "seguisb.ttf", disp, 40)
        f_url = _fuente_que_cabe(url, "consola.ttf", disp, 25)
        dc.text((xt, 44), titulo_txt, font=f_tit, fill=CREMA)
        dc.text((xt, 96), url, font=f_url, fill=TEAL_CLARO)
        img.alpha_composite(chip, (margen, cy))
    return img


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    piezas = (
        ("banner_youtube_2560x1440.png", banner_youtube),
        ("banner_ancho_1920x1080.png", banner_ancho),
        ("cuadrado_1080x1080.png", cuadrado),
        ("historia_1080x1920.png", historia),
    )
    for nombre, fn in piezas:
        img = fn()
        guardar(img, DESTINO / nombre)
        print(f"{nombre:32s} {img.width}x{img.height}  "
              f"{(DESTINO / nombre).stat().st_size // 1024:>5d} KB")


if __name__ == "__main__":
    main()
