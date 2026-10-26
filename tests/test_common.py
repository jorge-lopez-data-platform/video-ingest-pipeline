from scripts import common


def test_probe_lee_resolucion_y_audio(tmp_path):
    video = tmp_path / "v.mp4"
    common.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                       "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=2",
                       "-shortest", "-c:v", "libx264", "-c:a", "aac", "-y", str(video)])
    info = common.probe(video)
    assert info["ancho"] == 1080
    assert info["alto"] == 1920
    assert info["tiene_audio"] is True
    assert 1.5 <= info["duracion"] <= 2.5


def test_probe_sin_audio(tmp_path):
    video = tmp_path / "mudo.mp4"
    common.run_ffmpeg(["-f", "lavfi", "-i", "color=c=red:s=1080x1920:d=1",
                       "-c:v", "libx264", "-y", str(video)])
    assert common.probe(video)["tiene_audio"] is False
