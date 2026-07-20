"""Root shim — forwards to the package location."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))
from enamad.web.webapp import app  # noqa: F401,E402
