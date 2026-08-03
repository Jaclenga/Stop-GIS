# Shade-GIS - Shade Study Builder

Shade-GIS provides a modular and easy-to-configure open-source platform for preparing, reviewing, visualizing, and publishing bus stop shade studies. It is designed to reduce the effort required to manage transit stop data, shade classifications, reviewer decisions, public methodology copy, and deployable Streamlit study apps.

Shade-GIS follows a project-based builder approach that keeps local prototyping simple while preserving a path toward shared database-backed deployments. The platform lets researchers, transit agencies, municipalities, and community contributors focus on shade analysis and public communication rather than rebuilding the same data workflow for every study area.

| Category | Badges and Links |
| --- | --- |
| Tests | [Test workflow](.github/workflows/tests.yml) |
| License | [MIT License](LICENSE) |
| Documentation | [Platform schema](docs/platform_schema.md) - [Changelog](CHANGELOG.md) |
| Community | [Contributing](CONTRIBUTING.md) - [Support](SUPPORT.md) - [Governance](GOVERNANCE.md) |
| Citation | [CITATION.cff](CITATION.cff) |

## Table of Contents

- [Why use it?](#why-use-it)
- [How it works?](#how-it-works)
- [Getting Started](#getting-started)
- [Supported Data](#supported-data)
- [Bundled Example Dataset](#bundled-example-dataset)
- [Configuration](#configuration)
- [Testing](#testing)
- [Deployment](#deployment)
- [License and Citation](#license-and-citation)
- [Contact and Support](#contact-and-support)

## Why use it?

Shade studies often combine transit feeds, local imagery review, community observations, review decisions, GIS context, public-facing maps, and methodology documentation. Without a shared platform, each project tends to recreate the same glue code for imports, labeling, quality review, exports, and publication.

Shade-GIS addresses those recurring needs by providing:

- A reusable Streamlit builder for configuring study metadata, source data, shade source and coverage taxonomies, methodology copy, visualizations, optional public crowd voting, and exports.
- Flexible import paths for GTFS, CSV, GeoJSON, zipped Shapefiles, API-hosted files, and manually entered records.
- A raw labeling and admin review workflow that preserves submissions, agreement metrics, final labels, and audit history.
- A blind inter-rater workflow with versioned codebooks, neutral image IDs, randomized assignments,
  locked independent ratings, prespecified agreement thresholds, and identity-blinded adjudication.
- Project-scoped durable storage through SQLite by default, with a Postgres-ready relational schema for shared deployments.
- A generated public Streamlit app that can be bundled with current stops, raw labels, configuration, downloads, and methodology text.

Possible applications include:

- Citywide or corridor-level bus stop shade inventories.
- Community audit programs and reviewer quality workflows.
- Public maps for rider-facing shade information.
- Planning studies that combine shade conditions with ridership, routes, destinations, or other project-specific attributes.

## How it works?

Shade-GIS consists of the following layers:

- **Builder app** - The main Streamlit interface for importing data, editing project settings, reviewing labels, configuring visualizations, previewing the public app, and exporting deployment bundles.
- **Platform store** - A durable SQLite-backed project store that saves metadata, taxonomy, methodology, visualization settings, stops, import logs, raw labels, and review history.
- **Public app template** - The tested `published_app.py` experience plus the `public_voting.py` runtime copied into generated study bundles.
- **Schema and docs** - A Postgres-ready schema plus platform documentation for teams that want to move beyond local SQLite.

### Builder App

The builder starts with the bundled Tampa/HART starter dataset, or with a new imported dataset. Project teams can configure metadata, import logs, source licensing, shade source and coverage taxonomies, review workflow, map styling, dashboard sections, custom charts, public voting, public table columns, map hover fields, GIS overlays, and methodology content.

On the project list, use the `⋯` button on a project card to edit its name, agency, location, description, or visibility. Project deletion is in the same settings dialog and requires typing the exact project name; deleting the last project leaves an empty project list so a new study can be created without restoring the starter project.

The main app entrypoint is intentionally small:

```bash
streamlit run app.py
```

`builder_app.py` coordinates Streamlit state, navigation, and page rendering. Domain logic lives under
`shade_gis/`; deployment bundle assembly and generated scripts are grouped under `shade_gis/deploy/`.

The compact task header organizes the builder into `Dataset`, `Labelling`, `Preview`, and `Export`.
Dataset and other multi-page tasks expose a consistent second-level navigation row, while the
project selector stays at the right side of the header. Dataset's `Quality` page reports duplicate stop
IDs, missing coordinates, missing required fields, invalid point
geometries, and images that do not reference a stop in the active dataset. Each check shows an
affected-record count and a direct action that filters the paginated record viewer to the relevant
stop or image rows. The publication-readiness banner passes only when the dataset contains at least
one stop and every blocking check has zero findings. The Deploy workflow enforces the same report
before creating or publishing a website bundle. See [Data quality workflow](docs/data_quality.md)
for check definitions and remediation guidance.

`Dataset Status` separately summarizes total, labeled, reviewed, and needs-review stops. Label
coverage and review-completion progress bars make project completeness visible without scanning the
underlying table. A filtered, paginated work queue defaults to stops that need review or remain
unlabeled and shows only stop ID, workflow status, raw-label count, final label, and agreement. The
collapsed `Dataset Preview` renders only the selected 25-, 50-, or 100-row page while retaining
paginated access to every record.

The Preview's `Analytics` tab includes an Agreement section when `Agreement metrics` is selected in
the Visuals dashboard controls. Its compact overview shows labeled stops, unresolved disagreements,
mean agreement, Krippendorff's alpha, and Fleiss' kappa. The primary action opens a
disagreement-only queue sorted by lowest agreement, with minimum-label, agreement-threshold, and
label-category filters plus pagination. A reviewer can open one stop, compare every submitted label
and reviewer ID, inspect embedded Street View, the project map, and registered photos, then save a
canonical coverage/source decision. That decision marks the stop `Accepted` and appends an auditable
`Resolve disagreement` event to `review_history`; a newer raw label automatically reopens the stop.
Generated public apps show the compact summary and filtered disagreement queue without admin write
controls.

The Labelling task follows a clear `Dataset Review` → `Intercoder Review` → `Community Voting` pipeline.
`Dataset Review` is the moderation workspace: its default review queue resolves conflicts and verifies final
dataset decisions, while audit history remains read-only. Direct raw entry is retained as the
secondary `Add Administrative Label` action so it is not confused with structured research review.
The private `Intercoder Review` workspace supports formal reliability studies. A protocol can use
either standardized images or transit stops as its assessment unit. Image mode assigns every image
under a random `IMG-######` alias and withholds stop and geographic context. Stop mode assigns one
rating per stop under a `STOP-######` alias—even when several evidence images are attached—and can
support field surveys, Google Maps/Street View, or project imagery. Stop mode exposes location details
by design while continuing to hide existing labels, comments, other reviewers' answers, and aggregate
results. Every assignment accepts one immutable submission and records the evidence method used.
Agreement and consensus remain blocked until every assignment is submitted and coding closes.

After coding closes, Shade-GIS reports pairwise percent agreement and Krippendorff's alpha for each
codebook variable (ordinal for coverage and nominal for the others). Images with any variable below
the prespecified agreement threshold enter an adjudication queue. The adjudicator sees competing
code counts but not rater identities, and the final adjudication is stored separately without
altering the raw ratings. Administrator-only CSV downloads preserve the assessment unit, image/stop
linkage, evidence method, and rater IDs
for analysis, while a JSON download records the prespecified protocol.

The collapsed `Admin preview` control lets a trusted builder operator inspect administrator,
reviewer, and adjudicator interfaces; it is explicitly not an authentication system. Shared or
remote studies should put the builder behind access control and issue non-identifying reviewer IDs.
Images can still disclose location through visible signs or landmarks; crop those details, remove
EXIF/coordinate metadata before registration, and use neutral source filenames. The coding form
records whether a coder recognized the location so sensitivity analyses can exclude those ratings.

The Preview `Exports` tab and generated app downloads use a compact Export Files catalog instead of
stacked buttons. Every file row includes its purpose, record count, generated file size, relevant
data date, and download action. Import sources and timestamps appear separately below the catalog in
`Dataset Provenance`.

### Platform Store

The Streamlit builder uses SQLite by default. On Windows, the database is created under:

```text
%LOCALAPPDATA%\Shade-GIS\shade_study_builder.sqlite3
```

On other systems, the builder first tries:

```text
platform_data/shade_study_builder.sqlite3
```

Set `SHADE_GIS_DB_PATH` to point the builder at another writable SQLite database. If the configured or default database is readonly, the builder falls back to a writable user or temp database and shows the active path in the `Data` page.

### Public Study App

The `Preview` page renders the public-facing study experience for the active project. The `Deploy` page exports a GitHub-ready bundle containing:

- `app.py`: standalone public Streamlit app copied from `published_app.py`.
- `public_voting.py`: public voting interface and SQLite/PostgreSQL vote storage.
- `shade_study_stops.csv`: exported stop dataset with current priority scores.
- `shade_study_raw_labels.csv`: raw labels when labels have been collected.
- `shade_study_config.json`: project metadata, editable terminology, shade taxonomy, methodology, visualization settings, and import log.
- `requirements.txt`, `.streamlit/config.toml`, generated `README.md`, and `deploy_to_github.ps1`.

The dedicated `Community Voting` page places grouped visitor-voting configuration beside a responsive live
preview. An admin can enable or hide contributions, choose the
coverage categories visitors may submit, edit all visible voting copy including the separate shade-source
checkbox prompt, control whether a visitor may change an existing vote, show or hide totals, and set the minimum vote count
before a unique leading status is reported. Community results stay separate from the reviewed stop
dataset so public input does not silently overwrite an admin-approved classification.
Abuse prevention, result display, and deployment storage guidance are retained in collapsed groups;
environment-variable instructions no longer dominate the ordinary configuration workflow.

Robustness controls are enabled by default. The deployed app uses a server-keyed, one-way visitor
pseudonym to make session resets less useful, enforces a short cooldown, and caps how many new stops
the same pseudonymous visitor can vote on per hour. Raw IP addresses and browser headers are never stored.

Generated apps use a local `.shade_gis_votes.sqlite3` file by default. That is useful for local
testing, but hosted deployments should set the Streamlit secret
`SHADE_GIS_VOTE_DATABASE_URL = "postgresql://..."` because Streamlit Community Cloud local files
are ephemeral. The generated bundle README contains the complete setup note, and the app creates its
`shade_votes` table automatically. When request metadata is available, voter identifiers are keyed
HMAC pseudonyms derived from network/browser signals; those raw signals are not retained. The database
creates a private fingerprint key automatically, or deployments can provide a stable random
`SHADE_GIS_VOTE_FINGERPRINT_SECRET`. If request metadata is unavailable, the app falls back to a random
browser-session identifier. These controls raise the cost of casual manipulation but do not replace
authentication, CAPTCHA, or external abuse monitoring for high-stakes binding polls.

## Getting Started

Use the quick start below for a local builder run. For schema details, see [docs/platform_schema.md](docs/platform_schema.md).

### Prerequisites

- Python 3.11 or newer is recommended.
- A local shell capable of running Streamlit commands.
- Optional: Docker and Postgres if you want to test the shared database schema.

### Quick Start

Clone the repository and install runtime dependencies:

```bash
git clone https://github.com/OWNER/REPO.git
cd Shade-GIS
pip install -r requirements/requirements.txt
```

Start the builder:

```bash
streamlit run app.py
```

The app opens to the reusable builder. The bundled `stops.txt` and `shading_data.csv` seed the default starter project only; importing a GTFS zip, mapped file/API dataset, or manual entries in the `Data` page replaces the active in-session dataset.

### Local Folder Structure

The repository is organized around the reusable builder, generated public app template, tests, schemas, and documentation:

```text
Shade-GIS/
|-- app.py
|-- builder_app.py
|-- published_app.py
|-- shade_gis/                 # Importable Python package
|   |-- deploy/
|   |   |-- artifacts.py
|   |   |-- bundle.py
|   |   `-- templates/
|   `-- pages/
|-- docs/
|-- sql/
|-- scripts/
|-- tests/
|-- requirements/
`-- platform_data/
```

The deployment package separates control code from generated files: `bundle.py` assembles validated
ZIPs, `artifacts.py` fills deployment templates, and `templates/` contains the PowerShell and README
files users actually receive. Compatibility exports remain available from `builder_app.py`.

The repository uses the display name `Shade-GIS`, while Python code lives in `shade_gis/` because
Python package names cannot contain hyphens. There should not be another `Shade-GIS/` directory
inside the repository root.

`platform_data/` is used only when the default database path falls back to the repository. On Windows, the preferred runtime database location is outside OneDrive under `%LOCALAPPDATA%`.

## Supported Data

Required stop fields:

| Field | Description |
| --- | --- |
| `stop_id` | Unique stop identifier, usually from GTFS. |
| `stop_name` | Public stop name. |
| `stop_lat` | Stop latitude in WGS84. |
| `stop_lon` | Stop longitude in WGS84. |

Optional platform fields include `agency`, `routes`, `municipality`, `shading`, `shade_coverage`, `shade_sources`, `review_status`, `confidence`, `ridership`, and `nearby_destinations`.

Additional uploaded columns are preserved as dataset attributes. When those fields contain usable values, the builder can expose them in displays, map hovers, public filters, custom charts, colors, exports, and GIS overlay metadata without promoting them into the core platform schema.

Supported import paths:

| Source | Notes |
| --- | --- |
| GTFS ZIP | Requires `stops.txt`; route labels are enriched when `stop_times.txt`, `trips.txt`, and `routes.txt` are present. |
| CSV or `stops.txt` | Uses the field-mapping panel so agency-specific column names can be mapped into the platform schema. |
| GeoJSON | Reads FeatureCollections, Features, and geometry objects; point coordinates are mapped into stop longitude/latitude. |
| Zipped Shapefile | Upload a `.zip` containing at least `.shp` and `.dbf`; records are mapped through the same field-mapping panel. |
| API URL | Fetches CSV or GeoJSON from a URL and stores the source URL in the import log. |
| Manual entry | Provides an editable table for adding individual stops without a source file. |

## Bundled Example Dataset

The repository includes a small Tampa/HART starter study to demonstrate the builder workflow with real stop records. The example is not meant to be a complete published shade inventory. It is a handcrafted sample dataset: 34 bus stop datapoints were manually reviewed using Google Maps imagery, accepted by project admin Jack Lenga, and coded for visible shade conditions at the passenger waiting area.

The reviewed example records focus on three shade fields:

| Field | Meaning in the example dataset |
| --- | --- |
| `shade_coverage` | The amount of visible shade reaching the waiting area: `No Shade`, `Limited Shade`, or `Significant Shade`. |
| `shade_sources` | The visible source of shade reaching the waiting area, such as `Natural`, `Purpose-built`, `Incidental`, or combined labels when multiple sources are present. |
| `shading` | The derived coverage category used for coloring, filtering, summaries, and public display. |

Manual coding used the visible waiting area as the unit of analysis. Reviewers should code what appears to shade the place where a rider would reasonably stand or sit while waiting, rather than nearby objects that do not visibly shade that space.

Terminology:

| Term | Operational definition |
| --- | --- |
| `Waiting Area` | The designated location where passengers would reasonably stand or sit while waiting to board the bus, including any bus stop pad, sidewalk immediately adjacent to the bus stop sign, or seating within a bus shelter. Grass, landscaping, roadway, bicycle lanes, and areas not reasonably intended for waiting are excluded. |

The Taxonomy page in Dataset's second-level navigation provides compact inline editors for Terminology and both
columns in the Shade Source and Shade Coverage taxonomies. Project-facing labels and operational definitions are editable;
hidden canonical codes remain stable so renamed labels do not orphan existing labels, filters,
votes, or exports. Each source and coverage editor also provides a **Reset definitions** action
that restores the original operational definitions without replacing custom display labels.

The example labels distinguish source and coverage as separate dimensions. Use `shade_sources` for
what creates the shade and `shade_coverage` for how much of the waiting area is shaded. The derived
`shading` field mirrors the coverage code, and source values never appear as coverage choices or map
coverage labels.

Shade source definitions:

| Shade source | Operational definition |
| --- | --- |
| `Natural` | Trees, palms, hedges, or other vegetation visibly shade the waiting area. |
| `Purpose-built` | A designated bus shelter, awning, canopy, overhang, or similar passenger shelter visibly shades the waiting area. |
| `Incidental` | A nearby building or other non-shelter built feature visibly shades the waiting area. |

Shade coverage definitions:

| Shade coverage | Operational definition |
| --- | --- |
| `No Shade` | No shade visibly reaches the waiting area. |
| `Limited Shade` | Shade visibly reaches part of the waiting area, but does not cover most of it. |
| `Significant Shade` | Shade visibly covers most of the waiting area or seating area. |

Because the example was created from Google Maps imagery, it should be treated as a demonstration dataset with known limitations. Image dates, camera angle, season, time of day, temporary obstructions, and incomplete street-level coverage can all affect what shade is visible. The sample is useful for testing the platform workflow, previewing maps and review tools, and illustrating a reproducible coding approach; project teams should perform their own review before publishing a local study.

## Configuration

### Import Guardrails

Shade-GIS is intended for open-source use, so import safety is configurable rather than tied to one hosted environment.

Default limits:

```text
File and overlay uploads: 50 MB
API responses: 15 MB
ZIP members: 256
ZIP member size: 80 MB
ZIP total uncompressed size: 150 MB
```

Operators can adjust these defaults with:

```text
SHADE_GIS_MAX_UPLOAD_BYTES
SHADE_GIS_MAX_API_BYTES
SHADE_GIS_MAX_ZIP_MEMBERS
SHADE_GIS_MAX_ZIP_MEMBER_BYTES
SHADE_GIS_MAX_ZIP_UNCOMPRESSED_BYTES
```

API URLs must use `http` or `https`, cannot include embedded credentials, and cannot target localhost or private-network addresses by default. Use `SHADE_GIS_ALLOWED_API_HOSTS` for a host allowlist, or set `SHADE_GIS_ALLOW_PRIVATE_API_URLS=1` when intentionally running against private or local data services.

### Optional Postgres Schema

The Streamlit builder uses SQLite by default. `sql/schema.sql` mirrors the richer platform model for shared Postgres deployments with projects, stops, images, labels, releases, review history, taxonomy, settings, and import logs.

Start Postgres:

```bash
docker-compose up -d
```

Install optional database dependencies:

```bash
pip install -r requirements/requirements-db.txt
```

Initialize the schema and seed project stops:

```bash
python scripts/init_db.py
```

Default connection values are `localhost:5432`, database `tampa_shade`, user `postgres`, and password `postgres`. Override them with `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD`.

## Testing

Install the standard test dependencies:

```bash
pip install -r requirements/requirements-test.txt
```

Run the non-UI test suite:

```bash
pytest -q
```

The test suite uses temporary SQLite databases and small fixtures to verify project storage, imports, labels, review audit history, exports, priority scoring, and module syntax without touching the local builder database.

Browser UI tests are available as an opt-in Playwright suite:

```bash
pip install -r requirements/requirements-ui.txt
python -m playwright install chromium
pytest -q -m ui
```

Normal `pytest` runs exclude tests marked `ui`; GitHub Actions runs the UI suite in a separate job.

## Deployment

The repository-root `app.py` is the Shade-GIS builder entrypoint for local administration. Do not use it as the main file for a public study deployment.

To publish a rendered study, open the builder's **Deploy** page. Shade-GIS automatically detects the current repository, default branch, project readiness, and any known website address. The normal workflow has one action: **Publish app**. It then:

1. checks the project;
2. prepares a standalone website package;
3. commits and pushes only the generated website files from a clean temporary clone; and
4. polls and verifies the public website when its address is known.

Existing repositories receive the standalone runtime under `preview_app/` without replacing a repository-root Shade-GIS builder; the normal hosting entrypoint is `preview_app/app.py`. If the target root `app.py` is detected as an older generated public runtime, the publisher also upgrades that active root runtime in place so an existing Streamlit site cannot keep serving stale visualization code. The generated `shade_study_stops.csv`, optional `shade_study_raw_labels.csv`, and `shade_study_config.json` snapshots are refreshed at the repository root so older generated copies do not remain stale. A newly created preview-only repository uses `app.py` at its root. Every deployment includes the active imported and prepared stop dataset, including custom imported columns, so the public map, filters, metrics, and downloads use the same project snapshot as the builder preview. The legacy root `shading_data.csv` remains the builder starter seed and is not used by generated public apps. Repository, branch, hosting, build, diagnostic, and command details remain available under **Advanced settings** and **View technical details**.

An already-connected Streamlit Community Cloud app updates automatically after Shade-GIS pushes a new version. Streamlit requires a one-time browser authorization for the first deployment; the wizard links directly to that setup and then verifies the returned website address. Private repositories require the deployment host to have repository access. When public voting is enabled, add a PostgreSQL connection URL as the `SHADE_GIS_VOTE_DATABASE_URL` deployment secret before relying on vote persistence.

If automatic publishing is unavailable, expand **Advanced settings** and use **Download website package**. That manual fallback includes the guarded `deploy_to_github.ps1` helper and a generated README with the repository, branch, Downloads-folder discovery, authentication checks, and copy/paste-ready PowerShell command. Existing-repository mode still protects `.git/`, `.github/`, `.streamlit/`, root `README.md`, `LICENSE`, `.env*`, and `secrets.toml` from replacement.

## License and Citation

The code in this repository is licensed under the MIT License. See [LICENSE](LICENSE) for rights and obligations.

Use [CITATION.cff](CITATION.cff) when citing this repository or a derived study release.

## Contact and Support

For questions, bug reports, and project support expectations, see [SUPPORT.md](SUPPORT.md). For contribution workflow and governance expectations, see [CONTRIBUTING.md](CONTRIBUTING.md) and [GOVERNANCE.md](GOVERNANCE.md).
