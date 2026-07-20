"""Root shim — forwards to the package location."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))
import runpy, os  # noqa: E402
os.chdir(str(pathlib.Path(__file__).resolve().parent))
runpy.run_path(
    str(pathlib.Path(__file__).resolve().parent / "src/enamad/scraper/extract_enamad.py"),
    run_name="__main__",
)
