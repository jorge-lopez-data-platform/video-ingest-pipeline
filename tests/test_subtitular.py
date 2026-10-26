"""Tests de scripts/subtitular.py.

El caso que motivó el archivo está en `test_no_parte_una_frase`: el agrupador
genérico de transcribe.py producía "decides respirar. Así que cierra".
"""

from scripts import subtitular


def _palabras(*textos, paso=0.4):
    return [
        {"inicio": round(i * paso, 3), "fin": round(i * paso + paso * 0.7, 3), "texto": t}
        for i, t in enumerate(textos)
    ]


def test_no_parte_una_frase():
    palabras = _palabras("decides", "respirar.", "Así", "que", "cierra")
    chunks = subtitular.agrupar(palabras)
    assert chunks[0]["texto"] == "decides respirar."
    assert chunks[1]["texto"] == "Así que cierra"


def test_coma_no_corta_con_una_sola_palabra():
    palabras = _palabras("Dios,", "en", "serio.")
    chunks = subtitular.agrupar(palabras)
    assert chunks[0]["texto"] == "Dios, en serio."


def test_coma_corta_antes_de_la_palabra_siguiente():
    palabras = _palabras("que", "el", "sol,", "sale", "igual.")
    chunks = subtitular.agrupar(palabras)
    assert chunks[0]["texto"] == "que el sol,"
    assert chunks[1]["texto"] == "sale igual."


def test_respeta_maximo_de_palabras_y_caracteres():
    palabras = _palabras(*["palabra"] * 12)
    for chunk in subtitular.agrupar(palabras):
        assert len(chunk["texto"].split()) <= subtitular.MAX_PALABRAS
        assert len(chunk["texto"]) <= subtitular.MAX_CARACTERES


def test_no_se_pierde_ninguna_palabra():
    textos = ("uno", "dos.", "tres", "cuatro", "cinco", "seis", "siete.")
    chunks = subtitular.agrupar(_palabras(*textos))
    reunidas = " ".join(c["texto"] for c in chunks).split()
    assert reunidas == list(textos)


def test_los_tiempos_no_se_solapan_ni_invierten():
    chunks = subtitular.agrupar(_palabras("a", "b", "c", "d", "e", "f", "g", "h"))
    for chunk in chunks:
        assert chunk["fin"] > chunk["inicio"]
    for anterior, siguiente in zip(chunks, chunks[1:]):
        assert siguiente["inicio"] >= anterior["inicio"]


def test_texto_largo_se_parte_en_dos_lineas():
    resultado = subtitular._partir_en_dos_lineas("preocuparte por tonterías ya")
    assert r"\N" in resultado
    arriba, abajo = resultado.split(r"\N")
    assert abs(len(arriba) - len(abajo)) <= 4


def test_texto_corto_no_se_parte():
    assert subtitular._partir_en_dos_lineas("Respira.") == "Respira."


def test_ass_queda_sobre_la_zona_que_tapa_tiktok():
    """TikTok cubre los 420 px inferiores; MarginV debe superarlos."""
    assert subtitular.MARGEN_VERTICAL > 420


def test_generar_ass_contiene_los_dialogos():
    ass = subtitular.generar_ass([{"inicio": 0.0, "fin": 1.5, "texto": "hola"}])
    assert "[Events]" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:01.50,TikTok" in ass
    assert ass.rstrip().endswith("hola")


def test_generar_ass_escapa_el_salto_de_linea():
    ass = subtitular.generar_ass([{"inicio": 0.0, "fin": 1.0, "texto": "uno\ndos"}])
    dialogo = [l for l in ass.splitlines() if l.startswith("Dialogue")][0]
    assert "uno dos" in dialogo


# --- Marca de agua ---------------------------------------------------------

def _marcas(ass):
    return [l for l in ass.splitlines() if l.startswith("Dialogue: 2,")]


def _segundos(marca_tiempo):
    h, m, s = marca_tiempo.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def test_sin_usuario_no_se_quema_marca():
    """Un `@tu_usuario` de relleno sería peor que no llevar marca."""
    base = subtitular.generar_ass([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}])
    assert subtitular.agregar_marca(base, 30, "") == base
    assert subtitular.agregar_marca(base, 30, "   ") == base


def test_marca_anade_su_estilo_y_un_solo_dialogo():
    """Estática: un único evento para todo el video, no un evento por tramo."""
    base = subtitular.generar_ass([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}])
    ass = subtitular.agregar_marca(base, 40, "@alguienqueteescucha")
    assert f"Style: Marca,Arial,{subtitular.MARCA_TAMANO}" in ass
    assert len(_marcas(ass)) == 1


def test_marca_cubre_toda_la_duracion():
    base = subtitular.generar_ass([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}])
    evento = _marcas(subtitular.agregar_marca(base, 68.5, "@alguienqueteescucha"))[0]
    partes = evento.split(",")
    assert _segundos(partes[1]) == 0.0
    assert _segundos(partes[2]) == 68.5


def test_marca_no_se_mueve():
    """Se pide fija: ni `\\move` ni ningún otro efecto de posición animada."""
    base = subtitular.generar_ass([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}])
    evento = _marcas(subtitular.agregar_marca(base, 40, "@alguienqueteescucha"))[0]
    assert r"\move(" not in evento
    assert f"\\pos({subtitular.MARCA_PUNTO[0]},{subtitular.MARCA_PUNTO[1]})" in evento


def test_marca_cae_en_la_cintura_centrada():
    """El punto es el borde camisa/pantalón del personaje, en el centro del frame."""
    x, y = subtitular.MARCA_PUNTO
    from scripts import jesucitos

    assert x == subtitular.ANCHO // 2
    # La cintura está entre el final de la camisa y el principio del pantalón:
    # si el personaje se recoloca o se redimensiona, esto salta.
    assert jesucitos.PJ_Y < y < jesucitos.PJ_Y + jesucitos.ALTO_PJ
    # No se comprueba que quede sobre la franja visible de TikTok: la cintura
    # está a y≈1591 y la interfaz tapa los últimos 420 px. El usuario pidió la
    # cintura igualmente, así que se verifica solo que sea el borde exacto.
    assert 420 < y < subtitular.ALTO


def test_marca_anade_la_arroba_si_falta():
    base = subtitular.generar_ass([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}])
    sin_arroba = subtitular.agregar_marca(base, 10, "alguienqueteescucha")
    con_arroba = subtitular.agregar_marca(base, 10, "@alguienqueteescucha")
    assert _marcas(sin_arroba)[0].endswith("@alguienqueteescucha")
    assert _marcas(sin_arroba) == _marcas(con_arroba)


def test_marca_no_lleva_texto_adicional():
    """El usuario pidió eliminar el "contenido propio" bajo el arroba."""
    base = subtitular.generar_ass([{"inicio": 0.0, "fin": 1.0, "texto": "hola"}])
    evento = _marcas(subtitular.agregar_marca(base, 10, "@alguienqueteescucha"))[0]
    assert "contenido propio" not in evento
    assert r"\N" not in evento
