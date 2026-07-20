"""Package CLI entrypoint."""
from __future__ import annotations

import argparse
from typing import Sequence

from enamad.web.app_factory import create_app


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enamad package entrypoint")
    parser.add_argument(
        "--check-web",
        action="store_true",
        help="create app and verify core health route",
    )
    args = parser.parse_args(argv)

    if args.check_web:
        app = create_app()
        with app.test_client() as client:
            resp = client.get("/healthz")
            if resp.status_code != 200:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
