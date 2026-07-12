#!/usr/bin/env python3
"""Drop local enamad DB and import enamad.sql.gz (from server export)."""
from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
DUMP = ROOT / "enamad.sql.gz"
HOST = "127.0.0.1"
PORT = 3306
USER = "root"
PASSWORD = ""
DATABASE = "enamad"


def main() -> int:
    if not DUMP.is_file():
        print(f"Missing dump: {DUMP}", file=sys.stderr)
        return 1

    print(f"Connecting to {HOST}:{PORT} as {USER} (no password)...")
    admin = pymysql.connect(
        host=HOST, port=PORT, user=USER, password=PASSWORD, charset="utf8mb4"
    )
    try:
        with admin.cursor() as cur:
            print(f"Dropping database `{DATABASE}` if exists...")
            cur.execute(f"DROP DATABASE IF EXISTS `{DATABASE}`")
            cur.execute(
                f"CREATE DATABASE `{DATABASE}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        admin.commit()
    finally:
        admin.close()

    print(f"Importing {DUMP} ({DUMP.stat().st_size // 1024 // 1024} MB compressed)...")
    conn = pymysql.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=PASSWORD,
        database=DATABASE,
        charset="utf8mb4",
        autocommit=False,
    )
    buf: list[str] = []
    executed = 0
    try:
        with gzip.open(DUMP, "rt", encoding="utf-8", errors="replace") as fh:
            with conn.cursor() as cur:
                cur.execute("SET FOREIGN_KEY_CHECKS=0")
                cur.execute("SET UNIQUE_CHECKS=0")
                cur.execute("SET NAMES utf8mb4")
                for line in fh:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("--") or stripped.startswith("/*"):
                        continue
                    buf.append(line)
                    if stripped.endswith(";"):
                        sql = "".join(buf)
                        buf.clear()
                        try:
                            cur.execute(sql)
                        except pymysql.err.ProgrammingError as exc:
                            # Skip USE/CREATE DATABASE from dump header.
                            if exc.args and exc.args[0] in (1007, 1008, 1049):
                                continue
                            raise
                        executed += 1
                        if executed % 500 == 0:
                            conn.commit()
                            print(f"  ... {executed} statements")
                cur.execute("SET FOREIGN_KEY_CHECKS=1")
                cur.execute("SET UNIQUE_CHECKS=1")
        conn.commit()
    finally:
        conn.close()

    verify = pymysql.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=PASSWORD,
        database=DATABASE,
        charset="utf8mb4",
    )
    try:
        with verify.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM enamad_domains")
            domains = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM bot_users")
            users = cur.fetchone()[0]
    finally:
        verify.close()

    print(f"Done. statements={executed}, domains={domains:,}, bot_users={users}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
