#!/usr/bin/env python
"""Initialize Postgres database and populate a seed Stop-GIS project.

Usage:
  python scripts/init_db.py

It reads DB connection info from environment variables with sensible defaults.
"""
import os
import json
import psycopg2
from psycopg2.extras import execute_values
from pathlib import Path
import csv

DB_HOST = os.environ.get("PGHOST", "localhost")
DB_PORT = int(os.environ.get("PGPORT", 5432))
DB_NAME = os.environ.get("PGDATABASE", "tampa_shade")
DB_USER = os.environ.get("PGUSER", "postgres")
DB_PASS = os.environ.get("PGPASSWORD", "postgres")

HERE = Path(__file__).parent.parent
STOPS_FILE = HERE / "data" / "demo" / "stops.txt"
MIGRATIONS_DIR = HERE / "sql" / "migrations"

SCHEMA = "public"
PROJECT_ID = os.environ.get("STOP_GIS_PROJECT_ID") or os.environ.get(
    "SHADE_GIS_PROJECT_ID", "seed-tampa-stop-audit"
)


def connect():
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)


def load_stops_csv(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(
                (
                    PROJECT_ID,
                    r["stop_id"],
                    r.get("stop_name") or "Unnamed stop",
                    float(r.get("stop_lat")),
                    float(r.get("stop_lon")),
                    "Hillsborough Area Regional Transit (HART)",
                    json.dumps({"stop_code": r.get("stop_code")}),
                )
            )
    return rows


def main():
    rows = load_stops_csv(STOPS_FILE) if STOPS_FILE.exists() else []

    with connect() as conn:
        with conn.cursor() as cur:
            # ensure schema (tables) exist; schema.sql is executed by docker init, but double-check
            cur.execute((HERE / "sql" / "schema.sql").read_text(encoding="utf-8"))
            for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                cur.execute(migration_path.read_text(encoding="utf-8"))

            # Schema creation and migrations are required even when this optional
            # sample file is absent or empty.
            if not rows:
                conn.commit()
                if STOPS_FILE.exists():
                    print("No stops found in stops.txt; database schema is up to date.")
                else:
                    print("stops.txt not found at", STOPS_FILE, "- database schema is up to date.")
                return

            cur.execute(
                """
                INSERT INTO projects (
                    id, name, agency, region, description, owners, visibility,
                    dataset_version, methodology_version, source_name, source_license, source_url
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    agency = EXCLUDED.agency,
                    region = EXCLUDED.region,
                    description = EXCLUDED.description,
                    owners = EXCLUDED.owners,
                    visibility = EXCLUDED.visibility,
                    dataset_version = EXCLUDED.dataset_version,
                    methodology_version = EXCLUDED.methodology_version,
                    source_name = EXCLUDED.source_name,
                    source_license = EXCLUDED.source_license,
                    source_url = EXCLUDED.source_url,
                    updated_at = now()
                """,
                (
                    PROJECT_ID,
                    "Tampa Bus Stop Infrastructure Audit",
                    "Hillsborough Area Regional Transit (HART)",
                    "Tampa, Florida",
                    "Seed project for the reusable Stop-GIS platform.",
                    "Open transit and climate research contributors",
                    "Public",
                    "0.1.0",
                    "0.1.0",
                    "HART GTFS feed",
                    "Agency GTFS terms",
                    "",
                ),
            )

            # upsert seed stops into the project namespace
            sql = (
                "INSERT INTO stops (project_id, stop_id, stop_name, stop_lat, stop_lon, agency, extra_json) VALUES %s "
                "ON CONFLICT (project_id, stop_id) DO UPDATE SET "
                "stop_name = EXCLUDED.stop_name, "
                "stop_lat = EXCLUDED.stop_lat, "
                "stop_lon = EXCLUDED.stop_lon, "
                "agency = EXCLUDED.agency, "
                "extra_json = EXCLUDED.extra_json, "
                "updated_at = now()"
            )
            execute_values(cur, sql, rows)
        conn.commit()
    print(f"Inserted/updated {len(rows)} stops for project {PROJECT_ID} into {DB_NAME}.")


if __name__ == '__main__':
    main()
