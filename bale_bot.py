"""Root shim — forwards to the package location."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))
from enamad.bots.bale_bot import *  # noqa: F401,F403,E402
