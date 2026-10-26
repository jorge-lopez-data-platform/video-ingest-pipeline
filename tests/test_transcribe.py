from scripts import transcribe


def test_agrupa_respetando_pausa():
    palabras = [{"inicio": 0.0, "fin": 0.3, "texto": "uno"},
                {"inicio": 0.3, "fin": 0.6, "texto": "dos"},
                {"inicio": 2.0, "fin": 2.3, "texto": "tres"}]
    chunks = transcribe.agrupar_chunks(palabras)
    assert len(chunks) == 2
    assert chunks[0]["texto"] == "uno dos"


def test_palabra_unica_entre_pausas_se_emite():
    palabras = [{"inicio": 0.0, "fin": 0.2, "texto": "solo"},
                {"inicio": 5.0, "fin": 5.2, "texto": "otra"}]
    chunks = transcribe.agrupar_chunks(palabras)
    assert [c["texto"] for c in chunks] == ["solo", "otra"]


def test_chunks_respetan_maximo():
    palabras = [{"inicio": i * 0.3, "fin": i * 0.3 + 0.3, "texto": f"p{i}"} for i in range(12)]
    chunks = transcribe.agrupar_chunks(palabras)
    assert all(len(c["texto"].split()) <= 5 for c in chunks)


def test_verificar_duracion_avisa_por_desvio():
    assert transcribe.verificar_duracion(60.0, 61.0) is None
    assert transcribe.verificar_duracion(60.0, 30.0) is not None


def test_generar_ass_contiene_dialogos():
    ass = transcribe.generar_ass([{"inicio": 0.0, "fin": 1.5, "texto": "hola"}])
    assert "[Events]" in ass and "Dialogue:" in ass and "hola" in ass


def test_alucinacion_al_final_se_detecta():
    """Voz al inicio y un chunk fantasma al final de un audio con silencio.

    El span (último fin menos primer inicio) parece correcto; el tiempo
    realmente hablado delata la alucinación.
    """
    chunks = [{"inicio": 0.0, "fin": 0.5, "texto": "hola"},
              {"inicio": 55.0, "fin": 55.5, "texto": "subtítulos"}]
    assert transcribe.duracion_hablada(chunks) == 1.0
    assert transcribe.verificar_duracion(60.0, transcribe.duracion_hablada(chunks)) is not None


def test_duracion_hablada_no_cuenta_pausas():
    chunks = [{"inicio": 0.0, "fin": 10.0, "texto": "a"},
              {"inicio": 20.0, "fin": 30.0, "texto": "b"}]
    assert transcribe.duracion_hablada(chunks) == 20.0


def test_timestamp_no_produce_segundo_60():
    assert transcribe._timestamp(59.996) == "0:01:00.00"
    assert transcribe._timestamp(3599.999) == "1:00:00.00"


def test_duracion_desconocida_avisa_en_vez_de_desactivarse():
    assert transcribe.verificar_duracion(0.0, 12.0) is not None


def _chunks_densos(n=100, desde=0.0, hasta=60.0):
    paso = (hasta - desde) / n
    return [{"inicio": desde + i * paso, "fin": desde + i * paso + paso * 0.8,
             "texto": "palabra"} for i in range(n)]


def test_voz_continua_con_pausas_naturales_no_avisa():
    """Habla real: cada palabra dura menos que el hueco hasta la siguiente.

    Sumar solo el tiempo hablado daba 24% de desviación en un audio correcto.
    """
    chunks = _chunks_densos()
    assert transcribe.verificar_cobertura(60.0, chunks) is None


def test_alucinacion_dispersa_avisa():
    """Pocas palabras repartidas en 60 s: cobertura alta, densidad ínfima."""
    chunks = [{"inicio": t, "fin": t + 0.3, "texto": "x"} for t in (1.0, 30.0, 59.0)]
    assert transcribe.verificar_cobertura(60.0, chunks) is not None


def test_silencio_final_avisa():
    """El texto termina a los 20 s pero el audio dura 60: no cubre."""
    chunks = _chunks_densos(n=40, desde=0.0, hasta=20.0)
    assert transcribe.verificar_cobertura(60.0, chunks) is not None
