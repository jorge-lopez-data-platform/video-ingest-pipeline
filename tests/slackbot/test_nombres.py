from slackbot.nombres import canonico


def test_quita_el_sufijo_v2():
    assert canonico("film_v2.mp4") == "film.mp4"


def test_no_toca_un_nombre_sin_sufijo():
    assert canonico("film.mp4") == "film.mp4"


def test_solo_quita_el_sufijo_final():
    assert canonico("v2_film.mp4") == "v2_film.mp4"


def test_resiste_none_y_vacio():
    assert canonico(None) == ""
    assert canonico("") == ""
