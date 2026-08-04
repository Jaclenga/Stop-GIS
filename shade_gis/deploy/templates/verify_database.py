"""Verify the configured Shade-GIS voting database without printing credentials."""

from __future__ import annotations

import os
import sys

from public_voting import (
    VOTE_DATABASE_URL_ENV,
    check_vote_database_connection,
    confirm_vote_database_read_write,
)


def main() -> int:
    database_url = os.environ.get(VOTE_DATABASE_URL_ENV, "").strip()
    if not database_url:
        print(f"Set {VOTE_DATABASE_URL_ENV} in the local environment or host secret manager.")
        return 2
    try:
        check_vote_database_connection(database_url)
        confirm_vote_database_read_write(database_url)
    except Exception:
        print(
            "Database verification failed. Check PostgreSQL 14+, TLS, network access, "
            "schema initialization, and runtime-role permissions."
        )
        return 1
    print("PostgreSQL voting storage is reachable and runtime read/write access is confirmed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
