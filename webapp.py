"""Root shim — forwards to the package location."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))
from enamad.web.webapp import app, app_config  # noqa: F401,E402
