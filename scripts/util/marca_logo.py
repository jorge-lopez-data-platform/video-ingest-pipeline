"""Genera el logo y la marca de agua de @alguienqueteescucha con Pillow.

Salida (PNG con transparencia) en `tiktok_videos/marca/`:
- icono_blanco.png / icono_negro.png ............ símbolo solo, 512x512
- marca_horizontal_blanco.png / _negro.png ...... símbolo + nombre completo
- marca_agua.png ................................ versión pequeña para quemar en video
- avatar_800.png ................................ recorte cuadrado para perfil
- vista_previa.png .............................. muestra sobre fondo oscuro y claro

Uso: .venv/Scripts/python.exe scripts/marca_logo.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# El script se ejecuta como `python scripts/marca_logo.py`, así que sys.path[0]
# es `scripts/` y `import scripts.personaje` fallaría sin el repo en la raíz.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

FUENTES = Path("C:/Windows/Fonts")
DESTINO = Path("C:/Users/lopee/tiktok_videos/marca")

NOMBRE = "ALGUIEN QUE TE ESCUCHA"
ARROBA = "@alguienqueteescucha"
LEMA = "REFLEXIONES QUE SE SIENTEN"

BLANCO = (255, 255, 255, 255)
NEGRO = (17, 17, 17, 255)
TEAL = (29, 111, 102, 255)
CREMA = (246, 241, 232, 255)

# Recuadro del busto dentro del render de `personaje.dibujar()` (760x1000), con
# el brazo levantado y la boca cerrada. Medido sobre el render, no adivinado:
# incluye hombros y la mano alzada, que es lo que da la silueta reconocible.
# Zoom 1.12 sobre ese recuadro: llena el círculo sin cortar la mano alzada.
BUSTO_CENTRO = (380, 350)
BUSTO_BOX = (90, 60, 670, 640)
BUSTO_ZOOM = 1.0


def guardar(img: Image.Image, ruta: Path) -> None:
    """Guarda el PNG tolerando que el visor de Windows lo tenga abierto.

    Windows bloquea la escritura sobre un archivo abierto en Fotos. En vez de
    abortar toda la generación por una imagen en pantalla, se escribe como
    `_nuevo.png` y se avisa: el resto de piezas sí se regeneran.
    """
    try:
        img.save(ruta)
    except OSError:
        alterna = ruta.with_name(f"{ruta.stem}_nuevo{ruta.suffix}")
        img.save(alterna)
        print(f"  ! {ruta.name} está abierto; se guardó como {alterna.name}")


def _fuente(archivo: str, tam: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FUENTES / archivo), tam)


def _barra(d: ImageDraw.ImageDraw, cx: float, base: float, alto: float,
           ancho: float, radio: float, color) -> None:
    """Barra de onda sonora: rectángulo con extremos redondeados."""
    d.rounded_rectangle(
        [cx - ancho / 2, base - alto, cx + ancho / 2, base],
        radius=radio, fill=color,
    )


def dibujar_icono(lado: int, color) -> Image.Image:
    """Disco macizo con cinco barras de onda recortadas (transparentes).

    Se recortan en vez de pintarse para que el símbolo funcione sobre
    cualquier fondo sin necesidad de una segunda versión del archivo.
    """
    esc = 4  # se dibuja en grande y se reduce: bordes suaves sin antialias manual
    n = lado * esc
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    margen = int(n * 0.02)
    d.ellipse([margen, margen, n - margen, n - margen], fill=color)

    # Las barras se pintan con alfa 0 sobre el disco: recorte real.
    alturas = [0.30, 0.52, 0.72, 0.52, 0.30]
    ancho_barra = n * 0.085
    separacion = n * 0.165
    base = n * 0.665
    cx0 = n / 2 - separacion * (len(alturas) - 1) / 2
    recorte = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    dr = ImageDraw.Draw(recorte)
    for i, h in enumerate(alturas):
        _barra(dr, cx0 + separacion * i, base, n * h, ancho_barra,
               ancho_barra / 2, (255, 255, 255, 255))
    alfa = recorte.split()[3]
    img.putalpha(Image.composite(Image.new("L", (n, n), 0), img.split()[3], alfa))

    return img.resize((lado, lado), Image.LANCZOS)


def dibujar_personaje_badge(lado: int, color_disco=CREMA,
                            anillo: tuple | None = None,
                            grosor_anillo: int = 0) -> Image.Image:
    """El personaje de busto, con el brazo levantado, recortado en un círculo.

    Se compone sobre un lienzo opaco del color del disco antes de recortar: la
    capa del personaje trae un halo y, si se recorta en seco, ese halo queda
    como una niebla sucia alrededor de la cabeza.
    """
    from scripts.personaje import SPEC_DEFECTO, dibujar

    # brazo=-1 levanta el brazo del lado izquierdo, como en la referencia.
    render = dibujar(0.20, 0.0, -1.0, 0.0, 0.0, SPEC_DEFECTO)
    cx, cy = BUSTO_CENTRO
    hw = (BUSTO_BOX[2] - BUSTO_BOX[0]) / 2 / BUSTO_ZOOM
    hh = (BUSTO_BOX[3] - BUSTO_BOX[1]) / 2 / BUSTO_ZOOM
    recorte = (int(cx - hw), int(cy - hh), int(cx + hw), int(cy + hh))
    busto = render.crop(recorte).resize((lado, lado), Image.LANCZOS)

    relleno = Image.new("RGBA", (lado, lado), color_disco)
    relleno.alpha_composite(busto)

    ss = 4  # máscara a 4x: el borde del círculo no sale escalonado
    total = lado * ss
    mascara = Image.new("L", (total, total), 0)
    ImageDraw.Draw(mascara).ellipse([0, 0, total - 2, total - 2], fill=255)
    mascara = mascara.resize((lado, lado), Image.LANCZOS)

    disco = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    ImageDraw.Draw(disco).ellipse([0, 0, lado - 1, lado - 1], fill=color_disco)
    disco.alpha_composite(relleno.crop((0, 0, lado, lado)))
    disco.putalpha(Image.composite(disco.split()[3],
                                   Image.new("L", (lado, lado), 0), mascara))

    if not anillo or grosor_anillo <= 0:
        return disco

    # Anillo exterior: separa el círculo del fondo sin depender del color de
    # este. Sin él, sobre fondo blanco el disco crema desaparece.
    g = grosor_anillo
    con_anillo = Image.new("RGBA", (lado + g * 2, lado + g * 2), (0, 0, 0, 0))
    ImageDraw.Draw(con_anillo).ellipse(
        [0, 0, lado + g * 2 - 1, lado + g * 2 - 1], fill=anillo)
    con_anillo.alpha_composite(disco, (g, g))
    return con_anillo


def _texto_espaciado(d, xy, texto, fuente, color, espaciado) -> float:
    x, y = xy
    for ch in texto:
        d.text((x, y), ch, font=fuente, fill=color)
        x += d.textlength(ch, font=fuente) + espaciado
    return x


def _ancho_espaciado(d, texto, fuente, espaciado) -> float:
    return sum(d.textlength(c, font=fuente) for c in texto) + espaciado * (len(texto) - 1)


def dibujar_marca_horizontal(color, alto_circulo: int = 168) -> Image.Image:
    """Marca de una sola línea: círculo con el personaje + `@usuario`.

    Es la forma de la referencia del usuario: nada de nombre en mayúsculas ni
    lema debajo, solo el círculo a la izquierda y el arroba grande alineado a
    su centro vertical.
    """
    f_arriba = _fuente("seguisb.ttf", int(alto_circulo * 0.46))
    margen = alto_circulo * 0.06
    gap = alto_circulo * 0.30

    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    w_texto = tmp.textlength(ARROBA, font=f_arriba)

    ancho = int(margen * 2 + alto_circulo + gap + w_texto)
    alto = int(alto_circulo + margen * 2)
    img = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))

    disco = dibujar_personaje_badge(alto_circulo, CREMA, color, max(3, alto_circulo // 26))
    img.alpha_composite(disco, (int(margen), int(margen)))

    d = ImageDraw.Draw(img)
    caja = f_arriba.getbbox(ARROBA)
    x = margen + alto_circulo + gap
    y = margen + (alto_circulo - (caja[3] - caja[1])) / 2 - caja[1]
    d.text((x, y), ARROBA, font=f_arriba, fill=color)

    return img


def dibujar_marca_agua() -> Image.Image:
    """Marca pequeña para quemar en video: solo icono + arroba."""
    alto_icono = 96
    f = _fuente("seguisb.ttf", 52)
    d0 = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    w = d0.textlength(ARROBA, font=f)
    gap = 26
    margen = 8
    img = Image.new("RGBA", (int(margen * 2 + alto_icono + gap + w),
                              alto_icono + margen * 2), (0, 0, 0, 0))
    img.alpha_composite(dibujar_icono(alto_icono, BLANCO), (margen, margen))
    d = ImageDraw.Draw(img)
    caja = f.getbbox(ARROBA)
    y = margen + (alto_icono - (caja[3] - caja[1])) / 2 - caja[1]
    d.text((margen + alto_icono + gap, y), ARROBA, font=f, fill=BLANCO)
    return img


def _ajustar(p: Image.Image, ancho_max: int, alto_max: int) -> Image.Image:
    escala = min(ancho_max / p.width, alto_max / p.height, 1.0)
    return p.resize((int(p.width * escala), int(p.height * escala)), Image.LANCZOS)


def vista_previa(piezas) -> Image.Image:
    """Muestra las piezas sobre fondo oscuro y claro, ya escaladas al lienzo."""
    ancho, alto = 1200, 1000
    img = Image.new("RGBA", (ancho, alto), (10, 10, 12, 255))
    ImageDraw.Draw(img).rectangle([0, alto // 2, ancho, alto], fill=(245, 245, 247, 255))
    f = _fuente("seguisb.ttf", 22)

    # Zona oscura: marca horizontal (y 60-240), marca de agua (265-315), icono.
    img.alpha_composite(_ajustar(piezas[0], ancho - 120, 180), (60, 60))
    img.alpha_composite(_ajustar(piezas[1], ancho - 120, 52), (60, 265))
    img.alpha_composite(_ajustar(piezas[2], 120, 120), (60, 340))

    # Zona clara: la misma marca en negro.
    img.alpha_composite(_ajustar(piezas[3], ancho - 120, 170), (60, 580))

    d = ImageDraw.Draw(img)
    d.text((60, 18), "MARCA EN BLANCO (fondo oscuro)", font=f, fill=(200, 200, 206, 255))
    d.text((60, 520), "MARCA EN NEGRO (fondo claro)", font=f, fill=(110, 110, 118, 255))
    d.text((250, 365), "ICONO SUELTO 512 px", font=f, fill=(120, 120, 128, 255))
    return img


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)

    salidas = {}
    for nombre, color in (("blanco", BLANCO), ("negro", NEGRO)):
        icono = dibujar_icono(512, color)
        guardar(icono, DESTINO / f"icono_{nombre}.png")
        marca = dibujar_marca_horizontal(color)
        guardar(marca, DESTINO / f"marca_horizontal_{nombre}.png")
        salidas[nombre] = (icono, marca)

    agua = dibujar_marca_agua()
    guardar(agua, DESTINO / "marca_agua.png")
    # 3x para que ffmpeg la reduzca al quemar y no salgan bordes dentados.
    guardar(agua.resize((agua.width * 3, agua.height * 3), Image.LANCZOS),
            DESTINO / "marca_agua@3x.png")

    # Avatar: el personaje en el círculo, sobre fondo oscuro, cuadrado 800x800.
    avatar = Image.new("RGBA", (800, 800), (10, 10, 12, 255))
    avatar.alpha_composite(dibujar_personaje_badge(600, CREMA, BLANCO, 8), (100, 100))
    guardar(avatar, DESTINO / "avatar_800.png")

    piezas = [salidas["blanco"][1], agua,
              avatar.resize((260, 260), Image.LANCZOS), salidas["negro"][1]]
    guardar(vista_previa(piezas), DESTINO / "vista_previa.png")

    for f in sorted(DESTINO.glob("*.png")):
        print(f"{f.name:34s} {f.stat().st_size // 1024:>5d} KB")


if __name__ == "__main__":
    main()
