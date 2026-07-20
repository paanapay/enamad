"""Root shim — forwards to the package location."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))
from enamad.bots.telegram_bot import main  # noqa: E402
if __name__ == "__main__":
    raise SystemExit(main())
