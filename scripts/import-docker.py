#!/usr/bin/env python3
"""Import enamad.sql.gz into the Docker MariaDB container (docker compose mysql)."""
from __future__ import annotations

import gzip
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUMP = ROOT / "enamad.sql.gz"
CONTAINER = os.environ.get("MYSQL_CONTAINER", "enamad-mysql-1")
DATABASE = os.environ.get("MYSQL_DATABASE", "enamad")


def read_env_password() -> str:
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("MYSQL_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("MYSQL_PASSWORD", "changeme")


def docker_mysql(args: list[str], *, input_data: bytes | None = None) -> subprocess.CompletedProcess:
    cmd = [
        "docker", "exec", "-i", CONTAINER, "mariadb",
        "--default-character-set=utf8mb4",
        "-uroot", f"-p{PASSWORD}", *args,
    ]
    return subprocess.run(cmd, input=input_data, capture_output=True)


def main() -> int:
    global PASSWORD
    PASSWORD = read_env_password()

    if not DUMP.is_file():
        print(f"Missing dump: {DUMP}", file=sys.stderr)
        return 1

    print(f"Container: {CONTAINER}")
    print(f"Database: {DATABASE}")
    print(f"Dump: {DUMP} ({DUMP.stat().st_size // 1024 // 1024} MB)")

    print("=== drop & recreate database ===")
    sql = (
        f"DROP DATABASE IF EXISTS `{DATABASE}`; "
        f"CREATE DATABASE `{DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; "
        "SET GLOBAL foreign_key_checks=0;"
    )
    r = docker_mysql(["-e", sql])
    if r.returncode != 0:
        print(r.stderr.decode(errors="replace"), file=sys.stderr)
        return 1

    print("=== importing (may take 1-2 minutes) ===")
    proc = subprocess.Popen(
        [
            "docker", "exec", "-i", CONTAINER, "mariadb",
            "--default-character-set=utf8mb4",
            "-uroot", f"-p{PASSWORD}", DATABASE,
        ],
        stdin=subprocess.PIPE,
    )
    executed = 0
    buf: list[str] = []
    preamble = "SET NAMES utf8mb4; SET FOREIGN_KEY_CHECKS=0; SET UNIQUE_CHECKS=0;\n"
    if proc.stdin:
        proc.stdin.write(preamble.encode("utf-8"))
    with gzip.open(DUMP, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("--") or stripped.startswith("/*"):
                continue
            buf.append(line)
            if stripped.endswith(";"):
                sql = "".join(buf)
                buf.clear()
                if proc.stdin:
                    proc.stdin.write(sql.encode("utf-8"))
                    proc.stdin.flush()
                executed += 1
                if executed % 500 == 0:
                    print(f"  ... {executed} statements")

    if proc.stdin:
        proc.stdin.close()
    rc = proc.wait()
    if rc != 0:
        print(f"import failed (exit {rc})", file=sys.stderr)
        return 1

    print("=== verify ===")
    r = docker_mysql(["-N", "-e", f"SELECT COUNT(*) FROM enamad_domains;", DATABASE])
    if r.returncode == 0:
        print(f"domains: {r.stdout.decode().strip()}")
    print(f"done ({executed} statements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
