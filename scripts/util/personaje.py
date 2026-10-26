"""Personaje dibujado por capas, con una ficha de diseño que decide DeepSeek.

Comparte el lenguaje visual de `jesucitos.dibujar_personaje` (vector plano,
contornos gruesos, ojo grande con brillo) y exactamente los mismos parámetros
de animación, así que el motor de lip-sync no cambia.

La diferencia es que aquí el personaje **no es fijo**: recibe una ficha
(`spec`) con piel, pelo, ropa, accesorios y expresión, y la dibuja. Esa ficha
sale del guion (lo elige el LLM en `personaje_llm`), de modo que dos guiones
distintos producen dos personas distintas.

Se dibuja al doble y se reduce por el mismo motivo que el original: Pillow no
hace antialiasing en sus primitivas.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

from scripts.jesucitos import ALTO_PJ, ANCHO_PJ, ESCALA_PJ

# Ficha por defecto: la que se usa si el guion no tiene diseño asignado. Sirve
# también como red de seguridad si el LLM devuelve un campo inválido.
SPEC_DEFECTO = {
    "piel": "#E9B58F",
    "pelo_color": "#2B2018",
    "pelo_estilo": "corto",
    "ojos": "#4A3426",
    "ropa_tipo": "jersey",
    "ropa_color": "#1D6F66",
    "accesorio": "ninguno",
    "expresion": "calida",
    "escenario": "paraiso",
}

PELO_ESTILOS = ("corto", "ondulado", "rizado", "rapado", "coleta", "afro", "calvo")
ROPA_TIPOS = ("jersey", "camiseta", "camisa", "sudadera", "chaqueta")
ACCESORIOS = ("ninguno", "gafas", "gorra", "barba", "pendientes", "bufanda")
# Cada expresión cambia tres cosas a la vez: la curva de la ceja, la inclinación
# de las cejas y la altura del ojo. Una sola de ellas no basta para distinguir
# "sorprendido" de "asustado" a 1080 px.
EXPRESIONES = (
    "calida",      # cejas neutras, ojo normal — la cara base
    "sonriente",   # cejas arqueadas hacia arriba
    "seria",       # cejas bajas y rectas, ojo entrecerrado
    "sorprendida", # cejas muy altas, ojo muy abierto
    "reflexiva",   # cejas algo fruncidas hacia dentro, ojo medio cerrado
    "decidida",    # cejas bajas y juntas, mirada fija
    "preocupada",  # cejas inclinadas hacia arriba por dentro
    "inspirada",   # cejas altas y separadas, mirada al frente
)

# Geometría por expresión, en píxeles del dibujo base (escala 1).
#
# Se dibuja la ceja como un trazo entre dos puntos en vez de un `arc`: al mover
# solo los ángulos de un arco de PIL, los ocho gestos se veían prácticamente
# iguales en la hoja de contacto. Con extremos explícitos, `dintel` (altura del
# punto medio) controla el arqueo y la diferencia entre interior/exterior
# controla la inclinación, que es lo que lee el ojo.
#
#   alto       desplazamiento vertical de toda la ceja   (− = más arriba)
#   interior   altura del extremo de dentro de la cara   (− = más arriba)
#   exterior   altura del extremo de fuera               (− = más arriba)
#   dintel     altura del punto medio                    (− = más arqueada)
#   ojo        multiplica el alto de la esfera ocular
_GESTOS = {
    #            alto interior exterior dintel  ojo
    "calida":      {"alto": 0,  "interior": 0,  "exterior": 0,  "dintel": -6,  "ojo": 1.00},
    "sonriente":   {"alto": -4, "interior": 2,  "exterior": -4, "dintel": -12, "ojo": 0.92},
    "seria":       {"alto": 6,  "interior": 2,  "exterior": 2,  "dintel": 0,   "ojo": 0.80},
    "sorprendida": {"alto": -16,"interior": -2, "exterior": -2, "dintel": -16, "ojo": 1.30},
    "reflexiva":   {"alto": 2,  "interior": 8,  "exterior": -2, "dintel": -2,  "ojo": 0.86},
    "decidida":    {"alto": 7,  "interior": -6, "exterior": 6,  "dintel": 0,   "ojo": 0.80},
    "preocupada":  {"alto": -6, "interior": -12,"exterior": 6,  "dintel": -4,  "ojo": 1.06},
    "inspirada":   {"alto": -12,"interior": 0,  "exterior": 0,  "dintel": -14, "ojo": 1.14},
}

BOCA = (92, 30, 34, 255)
LENGUA = (214, 96, 106, 255)
DIENTES = (255, 255, 250, 255)
VAQUERO = (46, 64, 87, 255)
VAQUERO_SOMBRA = (35, 49, 67, 255)
ZAPATILLA = (240, 240, 236, 255)
ZAPATILLA_SUELA = (120, 120, 118, 255)


def _rgb(hexadecimal: str, defecto: tuple[int, int, int]) -> tuple[int, int, int]:
    """Convierte `#RRGGBB` a RGB; si no encaja, devuelve el color por defecto.

    El LLM escribe la ficha y puede devolver un nombre CSS o un hex con alfa.
    Un color inválido no debe tumbar un render de diez minutos, así que se
    degrada en silencio al valor por defecto.
    """
    try:
        texto = str(hexadecimal).strip().lstrip("#")
        if len(texto) == 8:
            texto = texto[:6]
        if len(texto) != 6:
            return defecto
        return (int(texto[0:2], 16), int(texto[2:4], 16), int(texto[4:6], 16))
    except (ValueError, TypeError):
        return defecto


def _oscurecer(rgb: tuple[int, int, int], factor: float = 0.78) -> tuple[int, int, int]:
    return tuple(int(c * factor) for c in rgb)  # type: ignore[return-value]


def _aclarar(rgb: tuple[int, int, int], factor: float = 1.22) -> tuple[int, int, int]:
    return tuple(min(255, int(c * factor)) for c in rgb)  # type: ignore[return-value]


def spec_normalizada(spec: dict | None) -> dict:
    """Completa y valida la ficha: cualquier campo raro cae al valor por defecto."""
    from scripts.escenarios import normalizar as normalizar_escenario

    s = dict(SPEC_DEFECTO)
    if spec:
        s.update({k: v for k, v in spec.items() if v not in (None, "")})
    if s["pelo_estilo"] not in PELO_ESTILOS:
        s["pelo_estilo"] = "corto"
    if s["ropa_tipo"] not in ROPA_TIPOS:
        s["ropa_tipo"] = "jersey"
    if s["accesorio"] not in ACCESORIOS:
        s["accesorio"] = "ninguno"
    if s["expresion"] not in EXPRESIONES:
        s["expresion"] = "calida"
    s["escenario"] = normalizar_escenario(s.get("escenario"))
    return s


def firma(spec: dict) -> str:
    """Identidad visual en una cadena, para detectar diseños repetidos.

    Incluye el escenario y la expresión: dos videos con la misma cara pero otro
    fondo y otra emoción no son "el mismo personaje", y el requisito de que
    siempre cambie se cumple igual.
    """
    s = spec_normalizada(spec)
    return "|".join(str(s[k]) for k in
                    ("piel", "pelo_color", "pelo_estilo", "ropa_tipo", "ropa_color",
                     "accesorio", "expresion", "escenario"))


def dibujar(apertura: float, parpadeo: float, brazo: float,
            bob: float, halo: float, spec: dict | None = None) -> Image.Image:
    """Dibuja el personaje según la ficha, con los mismos parámetros que el original.

    - apertura: 0 boca cerrada, 1 boca muy abierta (lo marca la energía del audio)
    - parpadeo: 0 ojos abiertos, 1 ojos cerrados
    - brazo: inclinación del brazo que gesticula, de -1 a 1
    - bob: desplazamiento vertical de todo el cuerpo en px
    - halo: 0 resplandor tenue, 1 resplandor intenso
    - spec: ficha de diseño; None usa la de por defecto
    """
    s = spec_normalizada(spec)
    e = ESCALA_PJ
    img = Image.new("RGBA", (ANCHO_PJ * e, ALTO_PJ * e), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx = (ANCHO_PJ // 2) * e
    bob_e = int(bob) * e

    piel = _rgb(s["piel"], (233, 181, 143))
    piel_sombra = _oscurecer(piel, 0.88)
    pelo = _rgb(s["pelo_color"], (43, 32, 24))
    pelo_luz = _aclarar(pelo, 1.7)
    ojos = _rgb(s["ojos"], (48, 34, 26))
    ropa = _rgb(s["ropa_color"], (29, 111, 102))
    ropa_sombra = _oscurecer(ropa)
    ropa_luz = _aclarar(ropa, 1.25)
    estilo_pelo = s["pelo_estilo"]
    tipo_ropa = s["ropa_tipo"]
    accesorio = s["accesorio"]
    expresion = s["expresion"]

    # Resplandor cálido detrás de la cabeza en lugar de la aureola: da la misma
    # separación del fondo sin el símbolo religioso. Va en capa propia porque
    # ImageDraw con fill RGBA *reemplaza* el píxel en vez de mezclarlo.
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    hx, hy = cx, 330 * e + bob_e
    for i in range(3):
        radio = (150 + (i + 1) * 40) * e
        gd.ellipse([hx - radio, hy - radio, hx + radio, hy + radio],
                   fill=(255, 214, 140, int(30 + 26 * halo)))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(26 * e)))
    d = ImageDraw.Draw(img)

    # Pelo largo/afro va DETRÁS de la cabeza; el resto, delante.
    if estilo_pelo == "afro":
        d.ellipse([cx - 210 * e, (330 - 200) * e + bob_e,
                   cx + 210 * e, (330 + 130) * e + bob_e], fill=pelo)
    elif estilo_pelo in ("coleta", "ondulado"):
        d.ellipse([cx - 190 * e, (330 - 168) * e + bob_e,
                   cx + 190 * e, (330 + 90) * e + bob_e], fill=pelo)

    # Cuello
    d.rounded_rectangle([cx - 58 * e, 420 * e + bob_e,
                         cx + 58 * e, 500 * e + bob_e],
                        radius=26 * e, fill=piel_sombra)

    # Mangas antes del torso, para que el torso tape su unión con el hombro.
    # La camiseta deja el brazo de piel: es lo que la distingue a distancia.
    color_manga = piel if tipo_ropa == "camiseta" else ropa_sombra
    hombro_izq = (cx - 92 * e, 520 * e + bob_e)
    # El abanico va de "brazo arriba" (-140°) a "brazo al costado" (-105°).
    angulo = math.radians(-140 + 35 * (brazo + 1) / 2)
    largo = 290 * e
    mano = (hombro_izq[0] + math.cos(angulo) * largo,
            hombro_izq[1] + math.sin(angulo) * largo)
    d.line([hombro_izq, mano], fill=color_manga, width=int(56 * e), joint="curve")

    hombro_der = (cx + 92 * e, 520 * e + bob_e)
    # La mano derecha queda fuera de la silueta del torso: pegada al borde
    # parecía un botón cosido a la ropa.
    mano_der = (cx + 232 * e, 790 * e + bob_e)
    d.line([hombro_der, mano_der], fill=color_manga, width=int(56 * e))

    # Torso. Hombros algo más anchos que la cintura: con el mismo ancho arriba
    # y abajo el cuerpo se leía como un embudo, no como una persona.
    d.polygon([cx - 126 * e, 486 * e + bob_e, cx + 126 * e, 486 * e + bob_e,
               cx + 146 * e, 560 * e + bob_e, cx + 122 * e, 828 * e + bob_e,
               cx - 122 * e, 828 * e + bob_e, cx - 146 * e, 560 * e + bob_e],
              fill=ropa)
    # Sombra del costado izquierdo: sin ella la prenda es una mancha plana.
    d.polygon([cx - 126 * e, 486 * e + bob_e, cx - 44 * e, 486 * e + bob_e,
               cx - 52 * e, 828 * e + bob_e, cx - 122 * e, 828 * e + bob_e],
              fill=ropa_sombra)

    if tipo_ropa == "camisa":
        # Cuello de camisa: dos triángulos y una línea de botones.
        d.polygon([cx - 62 * e, 470 * e + bob_e, cx - 6 * e, 470 * e + bob_e,
                   cx - 34 * e, 540 * e + bob_e], fill=ropa_luz)
        d.polygon([cx + 6 * e, 470 * e + bob_e, cx + 62 * e, 470 * e + bob_e,
                   cx + 34 * e, 540 * e + bob_e], fill=ropa_luz)
        for k in range(5):
            y = (580 + k * 48) * e + bob_e
            d.ellipse([cx - 7 * e, y - 7 * e, cx + 7 * e, y + 7 * e],
                      fill=ropa_luz)
    elif tipo_ropa == "sudadera":
        # Capucha plegada detrás del cuello.
        d.chord([cx - 96 * e, 448 * e + bob_e, cx + 96 * e, 560 * e + bob_e],
                start=0, end=180, fill=ropa_sombra)
        d.chord([cx - 70 * e, 462 * e + bob_e, cx + 70 * e, 540 * e + bob_e],
                start=0, end=180, fill=ropa)
    elif tipo_ropa == "chaqueta":
        # Chaqueta abierta sobre camiseta clara.
        d.polygon([cx - 60 * e, 486 * e + bob_e, cx + 60 * e, 486 * e + bob_e,
                   cx + 52 * e, 828 * e + bob_e, cx - 52 * e, 828 * e + bob_e],
                  fill=(236, 232, 224, 255))
        d.line([cx, 486 * e + bob_e, cx, 828 * e + bob_e],
               fill=ropa_sombra, width=int(10 * e))
    else:
        # Cuello redondo (jersey y camiseta)
        d.chord([cx - 68 * e, 458 * e + bob_e, cx + 68 * e, 534 * e + bob_e],
                start=0, end=180, fill=ropa_luz)

    # Bajo la prenda, un tramo de vaquero para que la cintura no flote
    d.rectangle([cx - 124 * e, 812 * e + bob_e, cx + 124 * e, 838 * e + bob_e],
                fill=VAQUERO)

    # Manos por encima del torso
    d.ellipse([mano[0] - 32 * e, mano[1] - 32 * e,
               mano[0] + 32 * e, mano[1] + 32 * e], fill=piel)
    d.ellipse([mano_der[0] - 30 * e, mano_der[1] - 30 * e,
               mano_der[0] + 30 * e, mano_der[1] + 30 * e], fill=piel)

    # Vaqueros: dos piernas separadas, no un bloque único. Separación
    # suficiente para que se vea el hueco: juntas se leían como una falda.
    for lado in (-1, 1):
        px = cx + lado * 72 * e
        d.polygon([px - 52 * e, 830 * e + bob_e, px + 52 * e, 830 * e + bob_e,
                   px + 48 * e, 934 * e + bob_e, px - 48 * e, 934 * e + bob_e],
                  fill=VAQUERO)
        d.polygon([px - 52 * e * lado, 830 * e + bob_e,
                   px - 14 * e * lado, 830 * e + bob_e,
                   px - 18 * e * lado, 934 * e + bob_e,
                   px - 48 * e * lado, 934 * e + bob_e], fill=VAQUERO_SOMBRA)

    # Cabeza
    hy_cabeza = 330
    r_cabeza = 148
    d.ellipse([cx - r_cabeza * e, (hy_cabeza - r_cabeza) * e + bob_e,
               cx + r_cabeza * e, (hy_cabeza + r_cabeza) * e + bob_e], fill=piel)

    # Pelo delantero según el estilo
    if estilo_pelo == "calvo":
        # Solo patillas cortas: sin casquete, la calva se lee de inmediato.
        d.ellipse([cx - 150 * e, (hy_cabeza - 40) * e + bob_e,
                   cx - 122 * e, (hy_cabeza + 40) * e + bob_e], fill=pelo)
        d.ellipse([cx + 122 * e, (hy_cabeza - 40) * e + bob_e,
                   cx + 150 * e, (hy_cabeza + 40) * e + bob_e], fill=pelo)
    else:
        altura = {"rapado": 8, "corto": 10, "rizado": 14,
                  "ondulado": 22, "coleta": 18, "afro": 26}.get(estilo_pelo, 10)
        # El borde inferior del casquete cae en el centro de este bbox (chord
        # 180→360 rellena la mitad superior). Con `+30` la línea del pelo
        # quedaba en hy-64 y tapaba las cejas, que son justo lo que distingue
        # una emoción de otra; con `-32` sube a hy-95 y deja verlas.
        d.chord([cx - (r_cabeza + 4) * e,
                 (hy_cabeza - r_cabeza - altura) * e + bob_e,
                 cx + (r_cabeza + 4) * e, (hy_cabeza - 32) * e + bob_e],
                start=180, end=360, fill=pelo)
        if estilo_pelo == "rizado":
            # Rizos: bolitas sobre el borde del casquete.
            for k in range(9):
                ang = math.radians(180 + k * 22.5)
                bx = cx + math.cos(ang) * (r_cabeza + 10) * e
                by = (hy_cabeza - 62) * e + math.sin(ang) * (r_cabeza + 10) * e + bob_e
                radio = 34 * e
                d.ellipse([bx - radio, by - radio, bx + radio, by + radio], fill=pelo)
        elif estilo_pelo == "ondulado":
            # Dos ondas laterales: sin ellas el casquete es un gorro.
            d.ellipse([cx - 168 * e, (hy_cabeza - 96) * e + bob_e,
                       cx - 108 * e, (hy_cabeza + 10) * e + bob_e], fill=pelo)
            d.ellipse([cx + 108 * e, (hy_cabeza - 96) * e + bob_e,
                       cx + 168 * e, (hy_cabeza + 10) * e + bob_e], fill=pelo)
        elif estilo_pelo == "coleta":
            # Coleta visible a un lado, detrás del cuello.
            d.ellipse([cx + 128 * e, (hy_cabeza + 40) * e + bob_e,
                       cx + 232 * e, (hy_cabeza + 230) * e + bob_e], fill=pelo)
        else:
            # Patillas pegadas al borde de la cara (sueltas parecían auriculares)
            d.ellipse([cx - 150 * e, (hy_cabeza - 112) * e + bob_e,
                       cx - 104 * e, (hy_cabeza + 18) * e + bob_e], fill=pelo)
            d.ellipse([cx + 104 * e, (hy_cabeza - 112) * e + bob_e,
                       cx + 150 * e, (hy_cabeza + 18) * e + bob_e], fill=pelo)
        # Volumen superior: un mechón levantado que rompe la simetría
        if estilo_pelo in ("corto", "ondulado", "coleta"):
            d.ellipse([cx + 10 * e, (hy_cabeza - 176) * e + bob_e,
                       cx + 132 * e, (hy_cabeza - 118) * e + bob_e], fill=pelo)
            d.ellipse([cx - 96 * e, (hy_cabeza - 158) * e + bob_e,
                       cx - 10 * e, (hy_cabeza - 122) * e + bob_e], fill=pelo_luz)

    if accesorio == "barba":
        # La barba sigue el mentón y no puede pasar de él: con el arco llegando
        # a hy+190 (el mentón está en hy+148) colgaba por debajo de la cara y se
        # leía como un collar negro, no como barba.
        d.chord([cx - (r_cabeza - 10) * e, (hy_cabeza + 26) * e + bob_e,
                 cx + (r_cabeza - 10) * e, (hy_cabeza + 150) * e + bob_e],
                start=0, end=180, fill=pelo)
        # Patillas: unen la barba con el pelo y evitan que quede una media luna
        # suelta sobre la cara.
        for lado in (-1, 1):
            px = cx + lado * (r_cabeza - 34) * e
            d.ellipse([px - 26 * e, (hy_cabeza + 10) * e + bob_e,
                       px + 26 * e, (hy_cabeza + 108) * e + bob_e], fill=pelo)
        # Bigote: dos trazos pegados bajo la nariz
        d.ellipse([cx - 54 * e, (hy_cabeza + 70) * e + bob_e,
                   cx - 2 * e, (hy_cabeza + 94) * e + bob_e], fill=pelo)
        d.ellipse([cx + 2 * e, (hy_cabeza + 70) * e + bob_e,
                   cx + 54 * e, (hy_cabeza + 94) * e + bob_e], fill=pelo)

    # Ojos. La expresión decide ceja, inclinación y apertura del ojo, que es lo
    # que más cambia la cara sin tocar la boca (la boca la manda el audio).
    gesto = _GESTOS[expresion]
    ojo_por = 46 * gesto["ojo"] * (1 - 0.92 * parpadeo)
    for lado in (-1, 1):
        ox = cx + lado * 52 * e
        d.ellipse([ox - 26 * e, (hy_cabeza - 26 - ojo_por) * e + bob_e,
                   ox + 26 * e, (hy_cabeza - 26 + ojo_por) * e + bob_e],
                  fill=(255, 255, 255, 255))
        if parpadeo < 0.6:
            d.ellipse([ox - 13 * e, (hy_cabeza - 34) * e + bob_e,
                       ox + 13 * e, (hy_cabeza - 8) * e + bob_e], fill=ojos + (255,))
            d.ellipse([ox - 6 * e, (hy_cabeza - 30) * e + bob_e,
                       ox + 1 * e, (hy_cabeza - 23) * e + bob_e],
                      fill=(255, 255, 255, 255))
        # Ceja como polilínea de tres puntos. Todo va por debajo de la línea del
        # pelo (hy-95) para que no la tape. `lado`=1 es la ceja derecha del
        # dibujo, así que su extremo interior es el izquierdo (menor x).
        base_y = hy_cabeza - 58 + gesto["alto"]
        x_ext = ox + lado * 30
        x_int = ox - lado * 30
        puntos = [
            (x_ext, (base_y + gesto["exterior"]) * e + bob_e),
            (ox, (base_y + gesto["dintel"]) * e + bob_e),
            (x_int, (base_y + gesto["interior"]) * e + bob_e),
        ]
        d.line(puntos, fill=pelo, width=int(9 * e), joint="curve")

        if accesorio == "gafas":
            d.rounded_rectangle([ox - 44 * e, (hy_cabeza - 60) * e + bob_e,
                                 ox + 44 * e, (hy_cabeza + 6) * e + bob_e],
                                radius=14 * e, outline=(38, 38, 42, 255),
                                width=int(7 * e))
    if accesorio == "gafas":
        # Puente entre las dos lentes
        d.line([cx - 8 * e, (hy_cabeza - 28) * e + bob_e,
                cx + 8 * e, (hy_cabeza - 28) * e + bob_e],
               fill=(38, 38, 42, 255), width=int(7 * e))

    if accesorio == "pendientes":
        for lado in (-1, 1):
            px = cx + lado * (r_cabeza - 6) * e
            d.ellipse([px - 13 * e, (hy_cabeza + 46) * e + bob_e,
                       px + 13 * e, (hy_cabeza + 72) * e + bob_e],
                      fill=(212, 175, 55, 255))

    if accesorio == "gorra":
        # Visera encima del pelo; se dibuja al final para tapar el casquete.
        d.chord([cx - 172 * e, (hy_cabeza - 190) * e + bob_e,
                 cx + 172 * e, (hy_cabeza - 40) * e + bob_e],
                start=180, end=360, fill=ropa_sombra)
        d.rounded_rectangle([cx - 168 * e, (hy_cabeza - 74) * e + bob_e,
                             cx + 168 * e, (hy_cabeza - 52) * e + bob_e],
                            radius=10 * e, fill=ropa)

    if accesorio == "bufanda":
        d.rounded_rectangle([cx - 92 * e, 470 * e + bob_e,
                             cx + 92 * e, 528 * e + bob_e],
                            radius=22 * e, fill=(196, 84, 78, 255))
        d.polygon([cx + 40 * e, 520 * e + bob_e, cx + 92 * e, 520 * e + bob_e,
                   cx + 84 * e, 640 * e + bob_e, cx + 44 * e, 640 * e + bob_e],
                  fill=(176, 72, 68, 255))

    # Nariz
    d.ellipse([cx - 12 * e, (hy_cabeza + 12) * e + bob_e,
               cx + 12 * e, (hy_cabeza + 40) * e + bob_e], fill=piel_sombra)

    # Boca: alto proporcional a la apertura del audio
    alto_boca = 8 + 62 * apertura
    ancho_boca = 42 + 16 * apertura
    by = (hy_cabeza + 106) * e + bob_e
    d.ellipse([cx - ancho_boca * e, by - (alto_boca / 2) * e,
               cx + ancho_boca * e, by + (alto_boca / 2) * e], fill=BOCA)
    if apertura > 0.35:
        # Lengua y dientes solo con la boca claramente abierta: cerrada
        # parecería una mancha.
        d.ellipse([cx - ancho_boca * 0.6 * e, by + alto_boca * 0.02 * e,
                   cx + ancho_boca * 0.6 * e, by + alto_boca * 0.5 * e], fill=LENGUA)
        d.rectangle([cx - ancho_boca * 0.7 * e, by - alto_boca * 0.5 * e,
                     cx + ancho_boca * 0.7 * e, by - alto_boca * 0.30 * e], fill=DIENTES)

    # Zapatillas: cuerpo redondeado, suela y cordones. Un polígono con la punta
    # hacia fuera quedó como una cuña blanca; la forma redondeada se lee como
    # calzado en una postura frontal, que es la única que hay aquí.
    for lado in (-1, 1):
        fx = cx + lado * 72 * e
        y0, y1 = 930, 968
        d.rounded_rectangle([fx - 58 * e, y0 * e + bob_e, fx + 58 * e, y1 * e + bob_e],
                            radius=26 * e, fill=ZAPATILLA)
        d.rounded_rectangle([fx - 60 * e, (y1 - 8) * e + bob_e,
                             fx + 60 * e, (y1 + 10) * e + bob_e],
                            radius=9 * e, fill=ZAPATILLA_SUELA)
        for k in range(3):
            y = (y0 + 10 + k * 8) * e + bob_e
            d.line([fx - 20 * e, y, fx + 20 * e, y], fill=ZAPATILLA_SUELA,
                   width=int(4 * e))

    return img.resize((ANCHO_PJ, ALTO_PJ), Image.Resampling.LANCZOS)
