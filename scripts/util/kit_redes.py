"""Kit de imágenes para las redes de @alguienqueteescucha.

Genera, en `C:/Users/lopee/tiktok_videos/kit_redes/`, las piezas que piden
Facebook, TikTok y YouTube: portadas, banners, avatares, miniaturas, iconos
y plantillas de publicación. Cada carpeta es una plataforma; `revision/`
guarda las variantes con las zonas seguras dibujadas y `hoja_revision.html`
las muestra a tamaño real con las medidas de cada plataforma.

No reinventa el dibujo: reutiliza `personaje.dibujar()` para el busto,
`escenarios.dibujar()` para los fondos y `marca_logo` para el icono y el
guardado tolerante a bloqueos de Windows.

Dos reglas del kit, deliberadas:
- El arroba va solo en piezas anchas (banner, portada, plantilla). En un
  avatar de 200 px serían 4 px de texto ilegible.
- La boca se cierra con `apertura=0.0`, no con `expresion`: la expresión solo
  mueve cejas y ojos. `marca_logo` usa 0.20 y por eso su avatar sale hablando.

Uso: .venv/Scripts/python.exe scripts/kit_redes.py
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# El script se ejecuta como `python scripts/kit_redes.py`: sin el repo en
# sys.path, `import scripts.personaje` falla.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from scripts import marca_logo  # noqa: E402
from scripts.marca_logo import (  # noqa: E402
    BUSTO_BOX,
    BUSTO_CENTRO,
    BUSTO_ZOOM,
    CREMA,
    NEGRO,
    TEAL,
    _fuente,
    _texto_espaciado,
    guardar,
)

DESTINO = Path("C:/Users/lopee/tiktok_videos/kit_redes")

ARROBA = "@alguienqueteescucha"
LEMA = "REFLEXIONES QUE SE SIENTEN"

FONDO = (10, 10, 12, 255)
TEAL_CLARO = (94, 186, 172, 255)
ORO = (235, 184, 104, 255)

# Textos de ejemplo. Van marcados con la etiqueta EJEMPLO en la pieza y en la
# hoja de revisión: son propuestas de maquetación, no guiones del usuario.
FRASES = (
    "NO TODO DOLOR\nES CASTIGO",
    "A VECES\nESCUCHAR\nYA ES SANAR",
    "EL SILENCIO\nTAMBIÉN\nRESPONDE",
)
ETIQUETA_EJEMPLO = "EJEMPLO · REEMPLAZAR TEXTO"


# --- utilidades -------------------------------------------------------------

def _lienzo(ancho: int, alto: int, color=FONDO) -> Image.Image:
    return Image.new("RGBA", (ancho, alto), color)


def _fondo_escena(nombre: str, ancho: int, alto: int,
                  velo: int = 150, blur: float = 16) -> Image.Image:
    """Escenario procedural desenfocado y oscurecido.

    El velo es lo que garantiza que el texto crema se lea sobre cualquier
    escena: sin él, una playa clara deja el titular sin contraste.
    """
    from scripts import escenarios

    fondo = escenarios.dibujar(nombre, ancho, alto).convert("RGBA")
    if blur:
        fondo = fondo.filter(ImageFilter.GaussianBlur(blur))
    velo_img = Image.new("RGBA", (ancho, alto), (8, 12, 14, velo))
    return Image.alpha_composite(fondo, velo_img)


def _franja_suave(ancho: int, alto: int, alto_franja: int,
                  alfa: int = 104) -> Image.Image:
    """Banda horizontal oscura con los bordes desvanecidos.

    El degradado se construye en una máscara `L` de una sola columna y se
    estira: dibujar el alfa pixel a pixel sobre 2560 px de ancho sería lento y
    no aportaría nada, porque la banda es uniforme en horizontal.
    """
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

    mascara = columna.resize((ancho, alto), Image.NEAREST)
    franja = _lienzo(ancho, alto, (6, 10, 12, 255))
    franja.putalpha(mascara)
    return franja


def _badge(lado: int, expresion: str = "calida", disco=CREMA,
           anillo=None, grosor: int = 0) -> Image.Image:
    """Busto del personaje recortado en círculo, con la boca cerrada.

    Copia la geometría de `marca_logo.dibujar_personaje_badge` pero fija
    `apertura=0.0`: en una foto de perfil, una boca a medio abrir se lee como
    una mueca. La capa del personaje se compone sobre un lienzo opaco antes de
    recortar para que el halo no quede como niebla alrededor de la cabeza.
    """
    from scripts.personaje import SPEC_DEFECTO, dibujar

    spec = dict(SPEC_DEFECTO)
    spec["expresion"] = expresion
    render = dibujar(0.0, 0.0, -1.0, 0.0, 0.0, spec)

    # `marca_logo.BUSTO_BOX` recorta en x=90 y la mano alzada llega a x=29: en
    # el círculo la mano salía amputada. Se usa un cuadrado centrado en la cara
    # (medido por color de piel y pelo, no a ojo) que sí la incluye entera.
    lado_recorte = 700
    cx = 380
    cy = 370
    x0 = max(0, min(render.width - lado_recorte, cx - lado_recorte // 2))
    y0 = max(0, min(render.height - lado_recorte, cy - lado_recorte // 2))
    recorte = (x0, y0, x0 + lado_recorte, y0 + lado_recorte)
    busto = render.crop(recorte).resize((lado, lado), Image.LANCZOS)

    relleno = Image.new("RGBA", (lado, lado), disco)
    relleno.alpha_composite(busto)

    ss = 4
    total = lado * ss
    mascara = Image.new("L", (total, total), 0)
    ImageDraw.Draw(mascara).ellipse([0, 0, total - 2, total - 2], fill=255)
    mascara = mascara.resize((lado, lado), Image.LANCZOS)

    circulo = _lienzo(lado, lado, (0, 0, 0, 0))
    ImageDraw.Draw(circulo).ellipse([0, 0, lado - 1, lado - 1], fill=disco)
    circulo.alpha_composite(relleno)
    circulo.putalpha(Image.composite(circulo.split()[3],
                                     Image.new("L", (lado, lado), 0), mascara))

    if not anillo or grosor <= 0:
        return circulo

    con_anillo = _lienzo(lado + grosor * 2, lado + grosor * 2, (0, 0, 0, 0))
    ImageDraw.Draw(con_anillo).ellipse(
        [0, 0, lado + grosor * 2 - 1, lado + grosor * 2 - 1], fill=anillo)
    con_anillo.alpha_composite(circulo, (grosor, grosor))
    return con_anillo


_MEDIDOR = ImageDraw.Draw(Image.new("RGBA", (8, 8)))


def _cabe(texto: str, fuente: ImageFont.FreeTypeFont, ancho: int,
          espaciado: float = 0.0) -> bool:
    if "\n" in texto:
        return all(_cabe(linea, fuente, ancho, espaciado)
                   for linea in texto.split("\n"))
    ancho_texto = _MEDIDOR.textlength(texto, font=fuente)
    ancho_texto += espaciado * max(0, len(texto) - 1)
    return ancho_texto <= ancho


def _fuente_que_cabe(texto: str, archivo: str, ancho: int, tam_max: int,
                     tam_min: int = 8, espaciado: float = 0.0):
    """Mayor tamaño de `archivo` con el que `texto` cabe en `ancho`.

    Se baja de uno en uno y no a ojo: los textos tienen anchos muy distintos y
    una fuente fija se sale en el más largo.
    """
    for tam in range(tam_max, tam_min - 1, -1):
        fuente = _fuente(archivo, tam)
        if _cabe(texto, fuente, ancho, espaciado):
            return fuente
    return _fuente(archivo, tam_min)


def _alto_texto(texto: str, fuente, interlinea: float = 1.0) -> float:
    caja = fuente.getbbox("Ág")
    return (caja[3] - caja[1]) * interlinea * len(texto.split("\n"))


def _etiqueta_ejemplo(d: ImageDraw.ImageDraw, x: int, y: int,
                      color=ORO) -> None:
    f = _fuente("consolab.ttf", 22)
    d.text((x, y), ETIQUETA_EJEMPLO, font=f, fill=color)
    d.line([x, y + 34, x + _MEDIDOR.textlength(ETIQUETA_EJEMPLO, font=f),
            y + 34], fill=color, width=2)


def _zonas(img: Image.Image, zonas, color=TEAL_CLARO) -> Image.Image:
    """Copia con las zonas seguras dibujadas (para revisión, no para subir).

    Pillow no tiene trazo discontinuo en `rectangle`, así que se dibuja a mano
    en tramos: es lo que distingue la guía de un borde real de la pieza.
    """
    copia = img.copy()
    d = ImageDraw.Draw(copia)
    f = _fuente("consolab.ttf", max(14, img.width // 60))
    paso = 18
    for (x0, y0, x1, y1, nombre) in zonas:
        for x in range(x0, x1, paso * 2):
            d.line([x, y0, min(x + paso, x1), y0], fill=color, width=3)
            d.line([x, y1, min(x + paso, x1), y1], fill=color, width=3)
        for y in range(y0, y1, paso * 2):
            d.line([x0, y, x0, min(y + paso, y1)], fill=color, width=3)
            d.line([x1, y, x1, min(y + paso, y1)], fill=color, width=3)
        d.text((x0 + 10, y0 + 8), nombre, font=f, fill=color)
    return copia


# --- piezas -----------------------------------------------------------------

def youtube_banner() -> Image.Image:
    """2560x1440. Todo lo importante dentro de 1546x423 centrados.

    YouTube recorta este banner distinto en móvil, TV y escritorio; el área
    segura es la única zona que sobrevive en las tres.
    """
    ancho, alto = 2560, 1440
    img = _fondo_escena("montana", ancho, alto, velo=138, blur=22)

    # Los bordes de la franja se desvanecen con una máscara de degradado: un
    # rectángulo plano deja dos líneas horizontales duras que se leen como un
    # error de montaje, no como una decisión.
    img.alpha_composite(_franja_suave(ancho, alto, 620))

    seguro_w, seguro_h = 1546, 423
    x0, y0 = (ancho - seguro_w) // 2, (alto - seguro_h) // 2

    lado = 300
    disco = _badge(lado, "calida", CREMA, CREMA, 6)
    img.alpha_composite(disco, (x0 + 20, y0 + (seguro_h - lado) // 2))

    x_texto = x0 + 20 + lado + 60
    ancho_texto = seguro_w - (x_texto - x0) - 20

    f_arroba = _fuente_que_cabe(ARROBA, "seguisb.ttf", ancho_texto, 150, espaciado=2)
    f_lema = _fuente_que_cabe(LEMA, "consolab.ttf", ancho_texto, 46, espaciado=8)
    d = ImageDraw.Draw(img)

    y = y0 + seguro_h / 2 - 108
    d.text((x_texto, y), ARROBA, font=f_arroba, fill=CREMA)
    d.line([x_texto, y + 176, x_texto + 200, y + 176], fill=ORO, width=5)
    _texto_espaciado(d, (x_texto, y + 208), LEMA, f_lema, TEAL_CLARO, 8)
    return img


def youtube_banner_revision() -> Image.Image:
    ancho, alto = 2560, 1440
    zonas = [
        ((ancho - 423) // 2, 0, (ancho + 423) // 2, alto, "MÓVIL 1:1 aprox."),
        ((ancho - 1546) // 2, (alto - 423) // 2,
         (ancho + 1546) // 2, (alto + 423) // 2, "ÁREA SEGURA 1546x423"),
    ]
    return _zonas(youtube_banner(), zonas)


def youtube_perfil() -> Image.Image:
    """800x800; YouTube lo muestra en círculo."""
    lado = 800
    img = _lienzo(lado, lado, FONDO)
    disco = _badge(700, "calida", CREMA, TEAL, 10)
    img.alpha_composite(disco, ((lado - disco.width) // 2, (lado - disco.height) // 2))
    return img


def miniatura(frase: str, indice: int) -> Image.Image:
    """1280x720. Titular serif a la izquierda, personaje a la derecha.

    En una miniatura el texto compite con el resto del feed a tamaño pulgar:
    tres o cuatro palabras por línea como máximo.
    """
    ancho, alto = 1280, 720
    escenas = ("noche", "abstracto", "biblioteca")
    img = _fondo_escena(escenas[indice % len(escenas)], ancho, alto, velo=168, blur=18)

    # Barra de acento a la izquierda: ancla la mirada aunque la miniatura se
    # vea a 168 px de ancho en el feed.
    ImageDraw.Draw(img).rectangle([0, 0, 18, alto], fill=TEAL)

    lado = 460
    disco = _badge(lado, "inspirada" if indice == 1 else "calida", CREMA, CREMA, 6)
    img.alpha_composite(disco, (ancho - lado - 46, (alto - lado) // 2 + 30))

    # El texto se dibuja en una capa aparte para poder centrarlo en vertical:
    # se mide primero sobre la capa transparente y luego se compone.
    capa = _lienzo(ancho, alto, (0, 0, 0, 0))
    dc = ImageDraw.Draw(capa)
    f = _fuente_que_cabe(frase, "georgiab.ttf", 660, 116, tam_min=40)
    espacio = int(f.size * 0.18)
    caja = dc.multiline_textbbox((74, 0), frase, font=f, spacing=espacio)
    y = (alto - (caja[3] - caja[1])) // 2 - caja[1]
    dc.multiline_text((74, y), frase, font=f, fill=CREMA, spacing=espacio)
    img.alpha_composite(capa)

    d = ImageDraw.Draw(img)
    d.text((74, alto - 96), ARROBA, font=_fuente("consolab.ttf", 30), fill=TEAL_CLARO)
    _etiqueta_ejemplo(d, 74, alto - 52)
    return img


def facebook_portada() -> Image.Image:
    """851x315. En móvil se recorta al centro: nada importante en los bordes."""
    ancho, alto = 851, 315
    img = _fondo_escena("noche", ancho, alto, velo=150, blur=14)
    lado = 190
    disco = _badge(lado, "calida", CREMA, TEAL, 6)
    img.alpha_composite(disco, (250, (alto - lado) // 2))

    d = ImageDraw.Draw(img)
    x = 250 + lado + 34
    disponible = ancho - x - 40
    d.text((x, alto / 2 - 48), ARROBA,
           font=_fuente_que_cabe(ARROBA, "seguisb.ttf", disponible, 44), fill=CREMA)
    _texto_espaciado(d, (x, alto / 2 + 6), LEMA,
                     _fuente_que_cabe(LEMA, "consolab.ttf", disponible, 20, 12),
                     TEAL_CLARO, 5)
    return img


def facebook_portada_revision() -> Image.Image:
    ancho, alto = 851, 315
    return _zonas(facebook_portada(), [
        ((ancho - 640) // 2, 10, (ancho + 640) // 2, alto - 10,
         "ZONA SEGURA MÓVIL 640x295"),
    ])


def avatar(lado: int, claro: bool) -> Image.Image:
    """Avatar cuadrado con fondo plano, para redes que no recortan en círculo."""
    # Fondo blanco, disco crema: con crema sobre crema el disco desaparecía y
    # solo quedaba un aro negro flotando.
    img = _lienzo(lado, lado, (255, 255, 255, 255) if claro else FONDO)
    anillo = NEGRO if claro else CREMA
    disco = _badge(int(lado * 0.86), "calida", CREMA, anillo, max(2, lado // 90))
    img.alpha_composite(disco, ((lado - disco.width) // 2, (lado - disco.height) // 2))
    return img


def publicacion(ancho: int, alto: int, escena: str = "abstracto",
                plantilla: bool = False) -> Image.Image:
    """Plantilla de publicación: fondo, bloque de texto y marca discreta."""
    img = _fondo_escena(escena, ancho, alto, velo=165, blur=20)

    margen = int(ancho * 0.09)
    frase = "TU FRASE\nAQUÍ" if plantilla else FRASES[0]
    f = _fuente_que_cabe(frase, "georgiab.ttf", ancho - margen * 2,
                         int(ancho * 0.15), tam_min=28)
    d = ImageDraw.Draw(img)
    espacio = int(f.size * 0.16)
    caja = d.multiline_textbbox((margen, 0), frase, font=f, spacing=espacio)
    alto_bloque = caja[3] - caja[1]
    y = (alto - alto_bloque) // 2
    d.multiline_text((margen, y - caja[1]), frase, font=f, fill=CREMA, spacing=espacio)

    y_linea = y + alto_bloque + int(alto * 0.035)
    d.line([margen, y_linea, margen + int(ancho * 0.14), y_linea],
           fill=ORO, width=max(3, ancho // 300))

    if plantilla:
        _etiqueta_ejemplo(d, margen, alto - int(alto * 0.075))
    else:
        d.text((margen, alto - int(alto * 0.075)), ARROBA,
               font=_fuente("consolab.ttf", max(12, ancho // 34)), fill=TEAL_CLARO)
    return img


def tiktok_portada() -> Image.Image:
    """1080x1920. Contenido lejos de la derecha (iconos) y del pie (texto)."""
    ancho, alto = 1080, 1920
    img = _fondo_escena("playa", ancho, alto, velo=120, blur=18)

    lado = 300
    disco = _badge(lado, "calida", CREMA, CREMA, 6)
    img.alpha_composite(disco, ((ancho - lado) // 2 - 150, 330))

    d = ImageDraw.Draw(img)
    centro = (ancho - 150) / 2
    d.text((centro, 690), ARROBA, anchor="ma",
           font=_fuente_que_cabe(ARROBA, "seguisb.ttf", 760, 64, espaciado=1), fill=CREMA)
    _texto_espaciado(d, (centro - 200, 790), LEMA,
                     _fuente_que_cabe(LEMA, "consolab.ttf", 700, 26, 12),
                     TEAL_CLARO, 6)
    d.line([centro - 90, 870, centro + 90, 870], fill=ORO, width=5)

    frase = FRASES[2]
    f2 = _fuente_que_cabe(frase, "georgiab.ttf", 800, 104)
    d.multiline_text((centro, 1080), frase, font=f2, fill=CREMA, anchor="ma",
                     align="center", spacing=int(f2.size * 0.16))
    return img


def tiktok_portada_revision() -> Image.Image:
    ancho, alto = 1080, 1920
    return _zonas(tiktok_portada(), [
        (0, 0, ancho, 240, "BARRA SUPERIOR"),
        (ancho - 180, 600, ancho, alto - 420, "ICONOS DERECHA"),
        (0, alto - 420, ancho - 180, alto, "TEXTO / MÚSICA"),
        (60, 260, ancho - 220, alto - 440, "CONTENIDO SEGURO"),
    ])


def tiktok_perfil() -> Image.Image:
    lado = 200
    img = _lienzo(lado, lado, FONDO)
    disco = _badge(lado - 8, "calida", CREMA, TEAL, 3)
    img.alpha_composite(disco, (4, 4))
    return img


def facebook_perfil() -> Image.Image:
    lado = 320
    img = _lienzo(lado, lado, FONDO)
    disco = _badge(lado - 12, "calida", CREMA, TEAL, 4)
    img.alpha_composite(disco, (6, 6))
    return img


def facebook_publicacion() -> Image.Image:
    """1200x630: la imagen que Facebook muestra al compartir un enlace."""
    ancho, alto = 1200, 630
    img = _fondo_escena("montana", ancho, alto, velo=150, blur=20)

    lado = 340
    disco = _badge(lado, "calida", CREMA, CREMA, 6)
    img.alpha_composite(disco, (80, (alto - lado) // 2))

    d = ImageDraw.Draw(img)
    x = 80 + lado + 46
    disponible = ancho - x - 50
    d.text((x, alto / 2 - 100), ARROBA,
           font=_fuente_que_cabe(ARROBA, "seguisb.ttf", disponible, 58), fill=CREMA)
    _texto_espaciado(d, (x, alto / 2 - 14), LEMA,
                     _fuente_que_cabe(LEMA, "consolab.ttf", disponible, 24, 12),
                     TEAL_CLARO, 6)
    d.line([x, alto / 2 + 60, x + 120, alto / 2 + 60], fill=ORO, width=5)
    return img


def favicon(lado: int) -> Image.Image:
    """Icono de onda sobre disco, en versiones cuadradas pequeñas."""
    img = _lienzo(lado, lado, FONDO)
    icono = marca_logo.dibujar_icono(int(lado * 0.92), CREMA)
    img.alpha_composite(icono, ((lado - icono.width) // 2, (lado - icono.height) // 2))
    return img


# --- revisión ---------------------------------------------------------------

def _contraste(a, b) -> float:
    """Ratio WCAG entre dos colores opacos (1 a 21)."""
    def luz(c):
        canales = []
        for v in c[:3]:
            v /= 255
            canales.append(v / 12.92 if v <= 0.04045
                           else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * canales[0] + 0.7152 * canales[1] + 0.0722 * canales[2]

    la, lb = luz(a), luz(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def hoja_revision(piezas) -> str:
    """HTML con cada pieza a tamaño real y sus medidas de plataforma."""
    filas = []
    for (ruta, titulo, medidas, nota) in piezas:
        rel = ruta.replace("\\", "/").split("kit_redes/")[-1]
        filas.append(f"""
    <figure>
      <figcaption><b>{titulo}</b><span>{medidas}</span></figcaption>
      <div class="shot"><img src="{rel}" alt="{titulo}"></div>
      <p>{nota}</p>
    </figure>""")

    pares = [("Crema sobre fondo", _contraste(CREMA, FONDO)),
             ("Crema sobre teal", _contraste(CREMA, TEAL)),
             ("Teal claro sobre fondo", _contraste(TEAL_CLARO, FONDO)),
             ("Oro sobre fondo", _contraste(ORO, FONDO)),
             ("Negro sobre crema", _contraste(NEGRO, CREMA))]
    contraste = "".join(
        f"<li>{n}: <b>{r:.1f}:1</b> "
        f"{'cumple AA' if r >= 4.5 else 'NO cumple AA'}</li>"
        for n, r in pares)

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kit de redes · alguienqueteescucha</title>
<style>
  :root {{
    --paper:#EFF2F1; --surface:#fff; --ink:#12191B; --ink2:#4C5A5D;
    --line:#D4DDDB; --accent:#1D6F66;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --paper:#0D1214; --surface:#141B1D; --ink:#EDF1F0;
             --ink2:#A7B4B4; --line:#2A3538; --accent:#5EBAAC; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:40px 24px 80px; background:var(--paper);
         color:var(--ink); font:15px/1.6 "Segoe UI",system-ui,sans-serif; }}
  h1 {{ font-size:clamp(26px,4vw,40px); margin:0 0 6px; letter-spacing:-.02em; }}
  h2 {{ font-size:22px; margin:44px 0 8px; }}
  .sub {{ color:var(--ink2); margin:0 0 34px; max-width:70ch; }}
  .grid {{ display:grid; gap:28px; grid-template-columns:1fr; }}
  figure {{ margin:0; background:var(--surface); border:1px solid var(--line);
            border-radius:14px; padding:18px; }}
  figcaption {{ display:flex; flex-wrap:wrap; gap:4px 14px;
                align-items:baseline; margin-bottom:12px; }}
  figcaption span {{ font:12px/1 ui-monospace,monospace; color:var(--accent);
                     letter-spacing:.06em; }}
  .shot {{ overflow:auto; background:#000; border-radius:8px; }}
  .shot img {{ display:block; max-width:100%; height:auto; }}
  figure p {{ margin:12px 0 0; color:var(--ink2); font-size:14px; }}
  ul {{ padding-left:18px; color:var(--ink2); }}
  ul b {{ color:var(--ink); }}
</style>
</head>
<body>
<h1>Kit de redes · {ARROBA}</h1>
<p class="sub">Cada imagen se muestra a su tamaño real. Las que llevan
«EJEMPLO» contienen texto de maquetación: reemplázalo por tu guion antes de
publicar.</p>
<div class="grid">{''.join(filas)}</div>
<h2>Contraste de texto (WCAG AA exige 4,5:1)</h2>
<ul>{contraste}</ul>
</body>
</html>
"""


# --- orquestación -----------------------------------------------------------

def main() -> None:
    carpetas = {n: DESTINO / n for n in
                ("youtube", "facebook", "tiktok", "comunes",
                 "plantillas", "revision")}
    for c in carpetas.values():
        c.mkdir(parents=True, exist_ok=True)

    salidas = []

    def exportar(img: Image.Image, carpeta: str, nombre: str) -> Path:
        ruta = carpetas[carpeta] / nombre
        guardar(img, ruta)
        salidas.append(ruta)
        return ruta

    # YouTube
    exportar(youtube_banner(), "youtube", "banner_2560x1440.png")
    exportar(youtube_banner_revision(), "revision", "banner_2560x1440_zonas.png")
    exportar(youtube_perfil(), "youtube", "perfil_800x800.png")
    for i, frase in enumerate(FRASES, 1):
        exportar(miniatura(frase, i - 1), "youtube", f"miniatura_{i}_1280x720.png")

    # Facebook
    exportar(facebook_portada(), "facebook", "portada_851x315.png")
    exportar(facebook_portada_revision(), "revision", "portada_851x315_zonas.png")
    exportar(facebook_perfil(), "facebook", "perfil_320x320.png")
    exportar(facebook_publicacion(), "facebook", "publicacion_enlace_1200x630.png")

    # TikTok
    exportar(tiktok_perfil(), "tiktok", "perfil_200x200.png")
    exportar(tiktok_portada(), "tiktok", "portada_video_1080x1920.png")
    exportar(tiktok_portada_revision(), "revision",
             "portada_video_1080x1920_zonas.png")

    # Comunes
    for lado in (512, 192, 32):
        exportar(favicon(lado), "comunes", f"favicon_{lado}.png")
    exportar(marca_logo.dibujar_icono(512, CREMA), "comunes", "icono_512.png")
    for lado in (800, 400, 200):
        exportar(avatar(lado, True), "comunes", f"avatar_{lado}_claro.png")
        exportar(avatar(lado, False), "comunes", f"avatar_{lado}_oscuro.png")

    # Plantillas
    exportar(publicacion(1080, 1080, "abstracto", True), "plantillas",
             "publicacion_cuadrada_1080x1080.png")
    exportar(publicacion(1080, 1920, "noche", True), "plantillas",
             "historia_1080x1920.png")
    exportar(_zonas(publicacion(1080, 1080, "abstracto", True), [
        (64, 64, 1016, 1016, "MARGEN 64 px")]), "revision",
        "publicacion_cuadrada_zonas.png")
    exportar(_zonas(publicacion(1080, 1920, "noche", True), [
        (72, 250, 1008, 1600, "ÁREA SEGURA HISTORIA")]), "revision",
        "historia_zonas.png")

    # Hoja de revisión: las rutas se vuelven relativas a DESTINO.
    piezas = [
        (str(carpetas["youtube"] / "banner_2560x1440.png"),
         "Banner de canal", "2560 × 1440 px · área segura 1546 × 423",
         "YouTube recorta distinto en móvil, escritorio y TV. Todo el texto "
         "vive dentro del área segura; el resto es decoración."),
        (str(carpetas["youtube"] / "perfil_800x800.png"),
         "Foto de canal", "800 × 800 px · se muestra en círculo",
         "El círculo recorta las esquinas: el disco lleva margen de sobra."),
        (str(carpetas["youtube"] / "miniatura_1_1280x720.png"),
         "Miniatura 1", "1280 × 720 px",
         "Texto de ejemplo. En el feed se ve a ~168 px de ancho: tres líneas "
         "cortas es el máximo legible."),
        (str(carpetas["youtube"] / "miniatura_2_1280x720.png"),
         "Miniatura 2", "1280 × 720 px", "Misma retícula, otra escena."),
        (str(carpetas["youtube"] / "miniatura_3_1280x720.png"),
         "Miniatura 3", "1280 × 720 px", "Misma retícula, otra escena."),
        (str(carpetas["facebook"] / "portada_851x315.png"),
         "Portada", "851 × 315 px · contenido en 640 × 295 centrados",
         "En móvil Facebook recorta los lados; el bloque está dentro de los "
         "640 px centrales."),
        (str(carpetas["facebook"] / "perfil_320x320.png"),
         "Foto de perfil", "320 × 320 px · se muestra en círculo", ""),
        (str(carpetas["facebook"] / "publicacion_enlace_1200x630.png"),
         "Imagen de enlace", "1200 × 630 px",
         "Es la que aparece al compartir el enlace del canal."),
        (str(carpetas["tiktok"] / "perfil_200x200.png"),
         "Foto de perfil", "200 × 200 px · se muestra en círculo",
         "A este tamaño el arroba sería ilegible: solo el personaje."),
        (str(carpetas["tiktok"] / "portada_video_1080x1920.png"),
         "Portada de video", "1080 × 1920 px",
         "Contenido desplazado a la izquierda para no quedar bajo los iconos "
         "de la derecha ni el texto del pie."),
        (str(carpetas["comunes"] / "icono_512.png"),
         "Icono de marca", "512 × 512 px", "Símbolo solo, sin personaje."),
        (str(carpetas["comunes"] / "favicon_32.png"),
         "Favicon", "32 × 32 px", "Se muestra a tamaño real."),
        (str(carpetas["comunes"] / "avatar_800_claro.png"),
         "Avatar claro", "800 × 800 px",
         "Variante para fondos claros; la oscura está en la misma carpeta."),
        (str(carpetas["plantillas"] / "publicacion_cuadrada_1080x1080.png"),
         "Plantilla cuadrada", "1080 × 1080 px",
         "Texto de ejemplo. Sustituye el bloque por el guion."),
        (str(carpetas["plantillas"] / "historia_1080x1920.png"),
         "Plantilla de historia", "1080 × 1920 px",
         "Texto de ejemplo. Sustituye el bloque por el guion."),
    ]
    (DESTINO / "hoja_revision.html").write_text(hoja_revision(piezas),
                                                encoding="utf-8")

    # ZIP por plataforma, sin la carpeta de revisión ni el propio zip.
    zip_ruta = Path("C:/Users/lopee/tiktok_videos/kit_redes.zip")
    with zipfile.ZipFile(zip_ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for ruta in sorted(salidas):
            if "revision" in ruta.parts:
                continue
            z.write(ruta, ruta.relative_to(DESTINO))
        z.write(DESTINO / "hoja_revision.html", "hoja_revision.html")

    print(f"{len(salidas)} PNG en {DESTINO}")
    print(f"ZIP listo para subir: {zip_ruta} "
          f"({zip_ruta.stat().st_size // 1024} KB)")
    for nombre, ratio in (("crema/fondo", _contraste(CREMA, FONDO)),
                          ("crema/teal", _contraste(CREMA, TEAL)),
                          ("teal claro/fondo", _contraste(TEAL_CLARO, FONDO)),
                          ("oro/fondo", _contraste(ORO, FONDO))):
        print(f"  contraste {nombre}: {ratio:.1f}:1 "
              f"{'OK' if ratio >= 4.5 else 'REVISAR'}")


if __name__ == "__main__":
    main()
