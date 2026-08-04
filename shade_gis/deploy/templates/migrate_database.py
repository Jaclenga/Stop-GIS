"""Run Shade-GIS voting migrations without printing database credentials."""

from __future__ import annotations

import os
import sys

from public_voting import VOTE_DATABASE_URL_ENV, initialize_vote_database


def main() -> int:
    database_url = os.environ.get(VOTE_DATABASE_URL_ENV, "").strip()
    if not database_url:
        print(f"Set {VOTE_DATABASE_URL_ENV} in the local environment or host secret manager.")
        return 2
    try:
        initialize_vote_database(database_url)
    except Exception:
        print(
            "Migration failed. Check PostgreSQL 14+, TLS, network access, owner-role "
            "permissions, and whether the database schema is newer than this app."
        )
        return 1
    print("Voting schema migration complete. Run scripts/verify_database.py with the runtime role next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
