# Stop-GIS

Stop-GIS is an open-source platform for building reproducible audits of public-transit stop infrastructure, amenities, accessibility, and passenger comfort.

Researchers, transit agencies, municipalities, and community organizations can configure a study, import stops, collect independent observations, review disagreements, analyze results on maps and dashboards, and publish a versioned Streamlit study without building a new data application for every audit.

## What Stop-GIS assesses

An administrator enables only the assessment modes needed by a project. Built-in definitions cover:

- benches, shelters, trash cans, lighting, and passenger information;
- sidewalk connections, boarding pads, wheelchair accessibility, curb ramps, and crosswalks;
- bike racks, cleanliness, and traffic exposure;
- coordinated shade coverage and shade source modes, including natural, purpose-built, and incidental shade;
- additional study-defined categorical, multi-select, boolean, numeric, or text attributes.

Each mode has a stable key, display label, description, operational definition, value type, allowed values, optional ordinal order, multi-select behavior, comment and confidence settings, required/enabled state, scoring metadata, and map/filter/summary/export visibility. The initial catalog is configuration rather than a fixed database schema, so new modes can be added without adding SQL columns.

## Starter templates

- **Basic Stop Amenities:** bench, shelter, trash can, lighting, and passenger information.
- **Accessibility Audit:** sidewalk connection, boarding pad, wheelchair accessibility, curb ramp, and crosswalk.
- **Passenger Comfort:** bench, shelter, shade coverage/source, lighting, cleanliness, and traffic exposure.
- **Custom:** start from the catalog and choose fields manually.

Templates are starting points. Labels, definitions, allowed values, requirements, order, display surfaces, and scoring can be changed per project.

## Research workflow

1. Create a project and select a template or configure modes manually.
2. Import stops from GTFS, CSV, GeoJSON, Shapefile, an HTTP API, or manual entry. Source data does not need assessment columns, and arbitrary imported attributes are preserved.
3. Collect immutable reviewer submissions with evidence method, comments, per-mode confidence, timestamps, and reviewer identity.
4. Review conflicts, retain the independent ratings, and append adjudications and review-history events.
5. Filter maps and inspect popups using enabled mode values. Build categorical counts and percentages, optionally grouped by route, municipality, agency, or imported attributes.
6. Export raw observations and current reviewed projections, or create a standalone deployment bundle.

## Data model

Stop-GIS uses an additive generic model:

- `assessment_modes` stores each project's JSON-compatible mode definitions.
- `assessments` stores immutable submissions with `assessment_values_json`, per-mode comments, and per-mode confidence.
- a stop's current reviewed projection is stored in its existing `extra_json`, including an `assessment_values` object, and materialized as ordinary columns for maps and exports.
- `review_history` retains moderator decisions.
- optional scoring profiles live in `project_settings.scoring_json`.

This avoids one permanent SQL column per amenity. Existing `shade_taxonomy`, `shade_labels`, `shading`, `shade_coverage`, and `shade_sources` identifiers remain as compatibility projections. They are not the architecture for new modes.

See [docs/platform_schema.md](docs/platform_schema.md) for schema and migration details.

## Shade compatibility

Shade remains a first-class assessment subject through two coordinated modes:

- `shade_coverage`: `none`, `limited`, `significant`, or `unclear` (ordinal);
- `shade_source`: `natural`, `purpose_built`, `incidental`, or `unclear` (multi-select nominal).

Opening an older project automatically creates these mode definitions from its shade taxonomy. Stored shade observations are not deleted or rewritten. New generic assessments update the legacy shade columns as projections when applied to the current stop view, so existing maps, voting deployments, and exports continue to work.

## Optional scores

Stop-GIS includes disabled-by-default Comfort and Accessibility score profiles. A profile explicitly records its included modes, weights, value-to-score mappings, and missing-value policy. Raw observations remain available and weights travel with the project configuration.

Missing and `unclear` observations are excluded by default. They become zero only when a project explicitly chooses the `zero` policy. Derived scores are study-specific analytical constructs—not objective, universal measures of stop quality.

## Inter-rater reliability

Independent and adjudicated submissions are distinct and append-only. Reliability calculations use independent submissions only. Mode definitions specify nominal, ordinal, interval, or ratio measurement levels, and generic reliability helpers calculate coincidence-weighted per-mode agreement and Krippendorff alpha using that level. The established blind shade-coding workflow remains available during the compatibility period.

## Demo data

The bundled Tampa/HART project demonstrates benches, shelters, trash cans, lighting, sidewalk connections, and shade coverage for a small subset of stops. Every added amenity observation is marked as example data. It is not a complete or current inventory of HART infrastructure and must not be presented as one.

## Installation

Python 3.11 or newer is recommended.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Run tests with:

```powershell
pytest -q
```

## Configuration

The primary local database variable is `STOP_GIS_DB_PATH`. If unset, Stop-GIS first discovers an existing legacy database and otherwise uses:

```text
%LOCALAPPDATA%\Stop-GIS\stop_gis_builder.sqlite3
```

The equivalent import limits are `STOP_GIS_MAX_UPLOAD_BYTES`, `STOP_GIS_MAX_API_BYTES`, `STOP_GIS_MAX_ZIP_MEMBERS`, `STOP_GIS_MAX_ZIP_MEMBER_BYTES`, and `STOP_GIS_MAX_ZIP_UNCOMPRESSED_BYTES`. API network controls are `STOP_GIS_ALLOWED_API_HOSTS` and `STOP_GIS_ALLOW_PRIVATE_API_URLS`.

Voting deployments use `STOP_GIS_VOTE_DATABASE_URL`, `STOP_GIS_VOTE_DB_PATH`, `STOP_GIS_VOTE_FINGERPRINT_SECRET`, `STOP_GIS_ALLOW_PRIVATE_DATABASE_HOSTS`, and `STOP_GIS_TRUST_PROXY_HEADERS`.

For backward compatibility, the corresponding `SHADE_GIS_*` variables are accepted when a new variable is not set. Existing `.shade_gis_votes.sqlite3`, `shade_study_*.csv/json`, `static/shade_gis_identity.json`, the `shade_votes` tables, and the `shade_gis` Python import package remain recognized so installed studies can update safely.

## Package layout

- `stop_gis/assessment_modes.py`: generic mode definitions, validation, filters, summaries, scoring, and reliability.
- `stop_gis/assessment_components.py`: builder and reviewer controls for configured modes.
- `shade_gis/`: compatibility implementation modules retained for existing imports and deployment scripts; new application imports resolve through `stop_gis`.
- `platform_store.py`: SQLite store and additive migration logic.
- `sql/schema.sql` and `sql/migrations/003_stop_gis_assessment_modes.sql`: PostgreSQL schema and migration.
- `builder_app.py`: project builder.
- `published_app.py`: generated public study runtime.

## Migration notes

SQLite migration runs on startup and only adds `project_settings.scoring_json`, `assessment_modes`, and `assessments`. It seeds shade modes for pre-existing projects and leaves all legacy rows in place. PostgreSQL deployments can apply `003_stop_gis_assessment_modes.sql`.

The compatibility layer intentionally retains old internal identifiers where renaming would break persisted databases, installed Android data, generated deployment ownership checks, or environment configuration. Public product text and new APIs use Stop-GIS terminology.

## License and citation

Stop-GIS is released under the MIT License. Cite the software version and archive the study configuration, mode definitions, raw observations, scoring profiles, and exported dataset used in an analysis. See [CITATION.cff](CITATION.cff).
