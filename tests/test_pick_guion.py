import pytest

from scripts import pick_guion


def test_descarta_confianza_media():
    filas = [{"confianza": "media", "hook": "a", "fuente_plays": 9},
             {"confianza": "alta", "hook": "b", "fuente_plays": 5}]
    assert pick_guion.seleccionar_guion(filas)["hook"] == "b"


def test_sin_guiones_altos_lanza_lookup():
    with pytest.raises(LookupError):
        pick_guion.seleccionar_guion([{"confianza": "media", "hook": "a"}])


def test_empata_por_plays_desc():
    filas = [{"confianza": "alta", "hook": "bajo", "fuente_plays": 1},
             {"confianza": "alta", "hook": "alto", "fuente_plays": 9}]
    assert pick_guion.seleccionar_guion(filas)["hook"] == "alto"


def test_construir_json_parte_campos_delimitados():
    fila = {"hook": "H", "hashtags": "a,b,c", "datos_a_verificar": "x | y",
            "texto_pantalla_1": "uno", "texto_pantalla_2": "", "texto_pantalla_3": "tres",
            "palabras_reales": 178}
    g = pick_guion.construir_guion_json(fila)
    assert g["hashtags"] == ["a", "b", "c"]
    assert g["datos_a_verificar"] == ["x", "y"]
    assert g["texto_en_pantalla"] == ["uno", "tres"]
    assert g["palabras_objetivo"] == 178
