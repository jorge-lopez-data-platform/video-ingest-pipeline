import json
from pathlib import Path

from scripts.render_job_json import render


def test_sustituye_usuario_en_rutas_anidadas():
    payload = {
        "new_settings": {
            "tasks": [{"notebook_task": {"notebook_path": "/Workspace/Users/USUARIO/x/nb"}}]
        }
    }
    out = render(payload, "ana@example.com", "tu_cuenta")
    ruta = out["new_settings"]["tasks"][0]["notebook_task"]["notebook_path"]
    assert ruta == "/Workspace/Users/ana@example.com/x/nb"


def test_sustituye_handle_por_defecto():
    payload = {"parameters": [{"default": "tu_cuenta", "name": "own_handles"}]}
    out = render(payload, "ana@example.com", "mi_cuenta")
    assert out["parameters"][0]["default"] == "mi_cuenta"


def test_no_toca_valores_no_texto():
    payload = {"job_id": 123, "activo": True, "nada": None}
    assert render(payload, "ana@example.com", "x") == payload


def test_no_muta_el_original():
    payload = {"ruta": "/Users/USUARIO/a"}
    render(payload, "ana@example.com", "x")
    assert payload == {"ruta": "/Users/USUARIO/a"}


def test_payloads_del_repo_son_json_valido_sin_bom():
    jobs = Path(__file__).resolve().parent.parent / "jobs"
    archivos = sorted(jobs.glob("*.job.json"))
    assert archivos, "no se encontraron payloads de job"
    for f in archivos:
        crudo = f.read_bytes()
        assert not crudo.startswith(b"\xef\xbb\xbf"), f"{f.name} tiene BOM"
        data = json.loads(crudo.decode("utf-8"))
        assert "job_id" in data and "new_settings" in data
