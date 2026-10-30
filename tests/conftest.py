import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURES.mkdir(exist_ok=True)

# El paquete `slackbot` vive en el repo tiktok-pipeline, no en este. Sin él,
# sus tests no pueden importar, así que se excluyen de la colección.
# Un paquete real tiene loader; tests/slackbot aparece como namespace package sin él.
spec = importlib.util.find_spec("slackbot")
if spec is None or spec.loader is None:
    collect_ignore_glob = ["slackbot/*"]
