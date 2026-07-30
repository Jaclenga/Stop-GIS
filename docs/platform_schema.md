# Shade Study Platform Schema

This document describes the durable project schema implemented by the Streamlit builder and the Postgres-ready relational schema in `sql/schema.sql`.

## Platform Backend

The builder uses a local SQLite database by default. On Windows, the database is created under
`%LOCALAPPDATA%\Shade-GIS\shade_study_builder.sqlite3` to avoid OneDrive file-locking issues; on
other systems it falls back to `platform_data/shade_study_builder.sqlite3`. Set `SHADE_GIS_DB_PATH`
to point the app at a different SQLite database file. The database stores multiple projects and
treats Streamlit session state as a live editing cache, not the durable source of record.
Deployment destination settings entered on the Deploy page are stored per project in
`project_settings.deployment_json`; they are restored when that project is reopened and are not
included in the generated public study configuration.

If the preferred SQLite file can be read but not written, the builder marks that path unusable for
the current process and retries against a writable user or temp fallback database. The active path is
shown in the `Data` page's Project Store controls.

The canonical relational shape is:

| Entity | Purpose |
| --- | --- |
| `projects` | One row per shade study, including publication metadata and source metadata. |
| `project_settings` | JSON methodology, visualization, and deployment settings for each project. |
| `shade_taxonomy` | Editable derived map category names, definitions, colors, and sort order. |
| `stops` | Per-project stop records, priority scores, review fields, and extra imported columns. |
| `shade_votes` | Deployed-app coverage votes and separate shade-source selections, isolated by study, stop, and browser-session voter ID. |
| `images` | Uploaded or referenced imagery associated with projects and stops. |
| `shade_labels` | Raw expert, crowd, imported, or model-assisted label submissions. |
| `review_history` | Status transitions, reviewer actions, notes, and audit metadata. |
| `releases` | Published or draft dataset/app release records and artifact manifests. |
| `import_logs` | Source, format, row count, timestamp, and import metadata. |

## Project

Each study is configured as a project.

| Field | Purpose |
| --- | --- |
| `name` | Public project name. |
| `agency` | Transit agency or agencies represented in the dataset. |
| `region` | City, county, metro area, or study geography. |
| `description` | Short project description. |
| `owners` | People or organizations responsible for the project. |
| `visibility` | Public or private publication status. |
| `dataset_version` | Version of the current stop and shade dataset. |
| `methodology_version` | Version of the current assessment method. |
| `source_name` | Primary transit dataset or source label. |
| `source_license` | License or terms for the source data. |
| `source_url` | Source URL when available. |

Methodology citation and bibliography text support grouped hanging-indent formatting with APA-style
templates: unindented lines render as group labels, and indented lines render as citation or
bibliography entries.

## Bus Stops

The builder accepts GTFS-compatible stops, mapped tabular files, spatial files, API responses, and manually entered stop records.

Supported import paths:

| Source | Current behavior |
| --- | --- |
| GTFS ZIP | Requires `stops.txt`; optional `stop_times.txt`, `trips.txt`, and `routes.txt` enrich route labels. |
| CSV or `stops.txt` | Uses the field-mapping panel before import. |
| GeoJSON | Converts feature geometry into `stop_lon` and `stop_lat`, preserves feature properties as mappable fields, and records geometry metadata in the import log. |
| Zipped Shapefile | Reads `.shp`/`.dbf` bundles through `pyshp`, converts geometry into `stop_lon` and `stop_lat`, and preserves attributes as mappable fields. |
| API URL | Fetches CSV or GeoJSON from a URL, then uses the same field-mapping panel. |
| Manual entry | Provides an editable table for adding individual stop records. |

Import guardrails are enforced before parsing so open-source deployments have safe defaults without
removing local flexibility. File and overlay uploads default to 50 MB; API responses default to
15 MB; ZIP uploads default to 256 members, 80 MB per member, and 150 MB total uncompressed size.
API URLs must use `http` or `https`, cannot include embedded credentials, and cannot target
localhost/private-network addresses unless `SHADE_GIS_ALLOW_PRIVATE_API_URLS=1` is set. Operators
can set `SHADE_GIS_ALLOWED_API_HOSTS` to a comma-separated host allowlist and can tune byte/member
limits with the `SHADE_GIS_MAX_*` environment variables documented in `README.md`.

Required fields:

| Field | Purpose |
| --- | --- |
| `stop_id` | Unique stop identifier. |
| `stop_name` | Human-readable stop name. |
| `stop_lat` | Latitude in WGS84. |
| `stop_lon` | Longitude in WGS84. |

Optional fields:

| Field | Purpose |
| --- | --- |
| `agency` | Agency label for multi-agency studies. |
| `routes` | Semicolon-separated route labels serving the stop. |
| `municipality` | Local jurisdiction or neighborhood label. |
| `shading` | Current derived coverage category used by maps and filters. |
| `shade_coverage` | Coverage dimension: `No Shade`, `Limited Shade`, or `Significant Shade`. |
| `shade_sources` | Semicolon-separated source dimension: `Natural`, `Purpose-built`, `Incidental`, or multiple values. |
| `review_status` | Workflow status such as unlabeled, accepted, or disputed. |
| `confidence` | Reviewer or model confidence. |
| `ridership` | Ridership measure used for prioritization. |
| `nearby_destinations` | Destination or nearby-place labels used for public map filtering. |

Columns outside the required and optional platform fields are preserved as dataset attributes in
`stops.extra_json`. They can be shown in tables, map hovers, color palettes, public filters, custom
charts, and exports when the active dataset contains usable values, but they are not promoted into
the core platform schema.

The Data page runs the active `stops` dataset and durable `images` registry through one centralized
`Data Quality` report. Duplicate stop IDs, missing coordinates, missing required fields, invalid
point geometries, and orphaned images are publication-blocking findings. Invalid point geometry
means a nonnumeric coordinate or a latitude/longitude outside WGS84 bounds; a missing coordinate is
reported separately. An image is orphaned when `images.stop_id` is blank or does not match an active
`stops.stop_id`. Every check exposes its count and can filter the affected-record viewer to the
source stop or image rows. A project is publication-ready only when it contains at least one stop
and the report contains no findings. The complete operator workflow is documented in
[`docs/data_quality.md`](data_quality.md).

The Data page also derives a compact `Dataset Status` dashboard from stop fields and raw-label history.
A stop counts as labeled when it has at least one raw label or a canonical coverage label. Reviewed
stops are labeled records whose `review_status` is crowd reviewed, expert reviewed, accepted, or
archived. Needs-review records include `Needs Review`, `Disputed`, unresolved raw-label conflicts,
and labeled records without a completed review state. The dashboard shows label coverage against
all stops and review completion against labeled stops. Its paginated work queue can filter Reviewed,
Needs Review, and Unlabeled records or search by stop ID. Stop data remains accessible in the
collapsed `Dataset Preview` expander, but only the selected 25-, 50-, or 100-row page is rendered;
the full dataset is never mounted as one browser table.

## Shade Taxonomies

The default terminology defines the unit used when assessing shade. Project editors can revise,
add, or remove these terms directly from the Data page:

| Term | Operational Definition |
| --- | --- |
| `Waiting Area` | The designated location where passengers would reasonably stand or sit while waiting to board the bus, including any bus stop pad, sidewalk immediately adjacent to the bus stop sign, or seating within a bus shelter. Grass, landscaping, roadway, bicycle lanes, and areas not reasonably intended for waiting are excluded. |

Terminology rows are stored with the project's methodology settings and exported as the top-level
`terminology` list in `shade_study_config.json`. Generated apps still accept the retired
`data_taxonomy` key when loading an older configuration, but new exports use `terminology` only.

The Taxonomy page in Dataset's second-level navigation lets project editors revise Shade Source and Shade Coverage
display labels and operational definitions inline. Each editable row retains a hidden canonical `code` because stored
labels, voting records, filters, and analytics depend on stable values. Project-facing coverage and
source labels persist with methodology settings and export through `shade_coverage_taxonomy` and
`shade_source_taxonomy`; coverage definitions also remain synchronized with `shade_taxonomy`.
The **Reset definitions** action restores the original source or coverage definitions while
preserving any project-specific display labels.

The core source taxonomy is:

| Shade Source | Operational Definition |
| --- | --- |
| `Natural` | Trees, palms, hedges, or other vegetation visibly shade the waiting area. |
| `Purpose-built` | A designated bus shelter, awning, canopy, overhang, or similar passenger shelter visibly shades the waiting area. |
| `Incidental` | A nearby building or other non-shelter built feature visibly shades the waiting area. |

The core coverage taxonomy is:

| Shade Coverage | Operational Definition |
| --- | --- |
| `No Shade` | No shade visibly reaches the waiting area. |
| `Limited Shade` | Shade visibly reaches part of the waiting area, but does not cover most of it. |
| `Significant Shade` | Shade visibly covers most of the waiting area or seating area. |

The `shading` field remains a derived map category for coloring, filtering, summaries, and public
display. It mirrors `shade_coverage`; source labels stay only in `shade_sources` so source and
coverage are independently queryable and never appear in the same choice list.

## Raw Shade Labels

The `Labels` page writes every submitted assessment to `shade_labels` instead of replacing earlier
labels. Each label records the stop ID, optional image reference, reviewer or contributor ID,
reviewer role, source type, derived coverage category, coverage, shade sources, confidence, notes, and
timestamp. A reviewer can optionally apply a submitted label to the current stop fields used by the
map and exports, but the raw label row is still retained either way.

The page also exposes raw-label history, basic counts for labeled/unlabeled/conflicting stops, and
a raw-label CSV download. The Agreement section in the Preview `Analytics` tab computes reliability
from `shade_category` labels:

- Per-stop majority label, label count, majority count, agreement percentage, disagreement flag, and tied-majority flag.
- Average pairwise Cohen's kappa using the latest label per stop per reviewer.
- Fleiss' kappa across stops with at least two raw labels.
- Nominal Krippendorff's alpha across stops with at least two raw labels.

Reviewer-based metrics use `labeler_id` when present; otherwise they fall back to the submitted
role/source combination. These metrics summarize reliability and do not overwrite raw labels or
current stop fields.

The Agreement overview replaces the former metrics table and shows those reliability measures
alongside the unresolved-disagreement count. Its primary action opens a queue containing only
unresolved disagreements, sorted by lowest agreement. The queue supports minimum-label,
agreement-threshold, and majority-category filters and paginates before rendering row actions. A
single-stop review shows all submitted labels and reviewer IDs, embedded Street View, the project
map, and all `images` rows associated with the stop. Saving a canonical coverage/source decision
updates the stop to `Accepted` and appends a `Resolve disagreement` event to `review_history`.
Resolution timestamps are compared with raw-label timestamps so a label submitted after the latest
decision reopens the stop automatically. Generated public apps render the compact overview and
filtered disagreement queue without canonical-decision controls.

## Review Workflow

The `Labels` page includes an admin review queue built from current stop statuses, raw-label counts,
agreement percentages, disagreement flags, and priority scores. Project teams can filter the queue
to stops that are unlabeled, disputed, or need review; search by stop ID/name/route; and isolate
stops with conflicting raw labels.

For each queued stop, an admin can accept the current label, enter an expert override, mark a stop
as disputed, resolve a dispute, or archive the stop. The decision form writes the final shade
category, coverage, source list, confidence, and review status back to the stop dataset used by maps
and exports. It also appends a `review_history` audit event with the actor, role, action, previous
and final statuses, previous and final labels, decision notes, agreement context, and timestamp.
Applying a raw label directly to the map label also records an audit event, so map-facing label
changes remain traceable.

Visualization settings store the selected map color field, premade and editable palettes for shade
categories, review statuses, priority-score gradients, and other categorical columns, plus marker
shape, size, opacity, outline, base map style, uploaded GIS overlays, data-backed context field
selections, up to 10 custom X/Y chart settings, advanced dashboard sections, public data
table/map-hover columns, and priority weights. Dataset-specific attributes are discovered from the
active stop table and remain project data, not schema-level platform fields.

`visualization.voting` stores the generated app's crowd-voting controls: enabled state, editable
heading/instructions/coverage-question/source-question/button/confirmation/result copy, allowed canonical coverage choices,
result visibility, minimum votes required before reporting a unique leader, and whether a browser
session may revise its vote. These controls and their deployed-interface preview live on the
builder's dedicated `Voting` page. Voting is hidden by default. Public consensus remains a separate signal
and does not overwrite the reviewed stop classification.

The generated app writes public observations to a `shade_votes` table keyed by `study_id`, `stop_id`,
and a random browser-session `voter_id`, with separate `coverage_status` and `shade_sources` values.
It uses `SHADE_GIS_VOTE_DATABASE_URL` for shared PostgreSQL
storage when configured and otherwise falls back to `.shade_gis_votes.sqlite3` beside the app.
Hosted deployments should configure PostgreSQL because Streamlit Community Cloud local files are
ephemeral. The runtime creates the table automatically and never exports database credentials.

Uploaded GIS overlays live under `visualization.gis_overlays`. Each overlay stores a name,
category, source, license, original filename, format, style settings, import timestamp, summary
metadata, and a GeoJSON FeatureCollection. GeoJSON files are preserved as GeoJSON, while zipped
Shapefiles are converted to GeoJSON features for rendering and export. These overlays render as
PyDeck `GeoJsonLayer`s in the builder preview and generated public app.

When `priority_score` is used by any configured visualization, the public methodology page
automatically includes the active priority formula. If `priority_score` is excluded from map
coloring, custom charts, dashboard summaries, public tables, and map-hover fields, the formula is
left out.

The Analytics tab renders the dashboard sections described in the platform issue when supporting
fields are available: summary statistics, shade distribution, stops without shade, stops requiring
review, agreement statistics, shade by route, shade by neighborhood, shade vs. ridership, and
highest-priority stops. Custom charts remain available below the dashboard.

## Preview Exports

The `Preview` page exports:

- Stops CSV.
- Stops GeoJSON.
- Raw labels CSV, when raw labels have been submitted.
- Study configuration JSON containing project metadata, the editable `terminology` definitions, shade taxonomy,
  methodology copy, visualization settings, and import log.

The Exports tab presents these files in one compact catalog. Each row explains the file's purpose,
record count, generated size, relevant import or label date, and download action. Raw Labels CSV
remains visible but disabled when no annotations exist, which keeps the export layout stable as the
project progresses. Dataset source, format, row count, and import timestamp are shown separately in
the `Dataset Provenance` section below the file catalog rather than as an unlabeled dataframe.

The `Deploy` page presents publishing as a four-stage wizard: `Check project`, `Prepare website`,
`Publish`, and `Verify website`. It detects the local GitHub repository and default branch, reduces
readiness failures to one outcome-level blocker, builds the public package, updates the repository
from a clean temporary clone, and verifies a known public URL. Existing connected Streamlit sites
redeploy automatically after the repository update. First-time Streamlit hosting still requires the
provider's one-time browser authorization.

The public Streamlit source is maintained in `published_app.py`, which the builder preview imports
and the deployment service packages as its standalone `app.py`. The package includes:

- Standalone `app.py` for the public Streamlit experience.
- `public_voting.py` with the configured crowd interface and vote-store implementation.
- `shade_study_stops.csv` with the active stop dataset and current priority scores.
- `shade_study_raw_labels.csv` with raw label submissions, when labels have been collected.
- `shade_study_config.json` with project metadata, editable terminology, shade taxonomy, methodology copy,
  visualization settings, and import log.
- `requirements.txt`, `.streamlit/config.toml`, generated `README.md`, `.gitignore`, and optional `deploy_to_github.ps1` helper.

Repository, branch, hosting, Git, PowerShell, workflow, package contents, and diagnostic output are
hidden under `Advanced settings` or `View technical details`. The ZIP download and generated
`deploy_to_github.ps1` helper remain available there as a manual fallback. The helper is generated
inside the package rather than the builder source tree and preserves the existing repository's
protected `.git/`, `.github/`, `.streamlit/`, root `README.md`, `LICENSE`, `.env*`, and
`secrets.toml` paths.

Import log `imported_at` values are stored as timezone-aware local timestamps with a UTC offset.
File imports also record the original filename when available; API imports record the source URL.

The public preview also includes user-facing map controls to search by stop name or stop ID, filter
routes, shade categories, review statuses, confidence, ridership, priority score, nearby
destinations, and eligible dataset-specific attributes when those fields exist, click markers to
select a stop, inspect a stop-detail panel, and show or hide stops whose shade label is still
`Needs Review` without changing the exported dataset.

## Review And Release Entities

`images` and `releases` remain durable schema foundations for richer evidence and publication
workflows. Raw label submission, admin review decisions, dispute resolution, expert overrides, and
review audit trails are exposed through the `Labels` page.
