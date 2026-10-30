"""El título tiene que salir distinto siempre, incluso cuando el LLM coopera poco."""

import json

from scripts.util import titulos


GUION = {
    "hook": "Siempre son los mismos.",
    "texto_pantalla_1": "overlay 0-5: ¿Controlas lo incontrolable?",
    "texto_pantalla_2": "Nadie te lo dice así",
    "texto_pantalla_3": "0-5: Descansar también entrena",
    "cierre": "Deja de pelear con lo que no depende de ti.",
}


class LLM_Falso:
    """Devuelve respuestas prefijadas; si se acaban, repite la última."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.llamadas += 1
        idx = min(self.llamadas - 1, len(self.respuestas) - 1)
        contenido = json.dumps({"titulos": self.respuestas[idx]})
        mensaje = type("M", (), {"content": contenido})
        return type("R", (), {"choices": [type("C", (), {"message": mensaje})]})


def test_normalizar_quita_placeholder_de_rango():
    assert titulos.normalizar("overlay 0-5: ¿Controlas lo incontrolable?") == \
        "¿Controlas lo incontrolable?"
    assert titulos.normalizar("0-5: Nadie te dice esto") == "Nadie te dice esto"


def test_normalizar_quita_hashtags_comillas_y_punto():
    assert titulos.normalizar('  "Nadie te lo dice #fyp"  ') == "Nadie te lo dice"
    assert titulos.normalizar("El amor es una decisión.") == "El amor es una decisión"


def test_normalizar_recorta_sin_partir_palabras():
    largo = " ".join(["palabra"] * 40)
    salida = titulos.normalizar(largo)
    assert len(salida) <= titulos.MAX_TITULO
    assert not salida.endswith("palab")


def test_clave_ignora_acentos_mayusculas_y_puntuacion():
    assert titulos.clave("¿Y si lo pierdes todo?") == titulos.clave("y si lo pierdes todo")
    assert titulos.clave("El amor es una decisión") == titulos.clave("EL AMOR ES UNA DECISION")


def test_desde_guion_normaliza_y_no_repite():
    opciones = titulos.desde_guion(GUION)
    assert "¿Controlas lo incontrolable?" in opciones
    # El placeholder de texto_pantalla_3 se limpia igual que el resto.
    assert "Descansar también entrena" in opciones
    assert len(opciones) == len({titulos.clave(o) for o in opciones})


def test_elegir_devuelve_el_primero_si_no_esta_usado():
    llm = LLM_Falso([["El precio de controlarlo todo"]])
    elegido = titulos.elegir(llm, "modelo", GUION, evitar=["Otro título"])
    assert elegido == "El precio de controlarlo todo"


def test_elegir_salta_los_repetidos_aunque_cambien_acentos():
    llm = LLM_Falso([
        ["El precio de controlarlo todo"],    # mismo título, otra caja
        ["Controlarlo todo te cuesta caro"],  # nuevo en el segundo intento
    ])
    elegido = titulos.elegir(llm, "modelo", GUION,
                             evitar=["el precio de controlarlo TODO"])
    assert elegido == "Controlarlo todo te cuesta caro"
    assert llm.llamadas == 2


def test_elegir_cae_al_guion_si_el_llm_insiste():
    repetido = "El precio de controlarlo todo"
    llm = LLM_Falso([[repetido], [repetido], [repetido]])
    elegido = titulos.elegir(llm, "modelo", GUION, evitar=[repetido])
    # No es el repetido: viene del propio guion, ya normalizado.
    assert titulos.clave(elegido) != titulos.clave(repetido)
    assert elegido == "¿Controlas lo incontrolable?"


def test_elegir_desambigua_si_todo_el_guion_esta_usado():
    todos = titulos.desde_guion(GUION)
    llm = LLM_Falso([todos, todos, todos])
    elegido = titulos.elegir(llm, "modelo", GUION, evitar=todos)
    assert titulos.clave(elegido) not in {titulos.clave(t) for t in todos}
    assert elegido  # nunca vacío: YouTube no publica sin título


def test_elegir_aguanta_respuesta_no_parseable():
    class LLM_Roto(LLM_Falso):
        def create(self, **kwargs):
            self.llamadas += 1
            mensaje = type("M", (), {"content": "no es json"})
            return type("R", (), {"choices": [type("C", (), {"message": mensaje})]})

    elegido = titulos.elegir(LLM_Roto([[]]), "modelo", GUION)
    assert elegido == "¿Controlas lo incontrolable?"
