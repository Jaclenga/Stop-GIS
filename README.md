# Shade-GIS

**An open-source platform for building reproducible studies of shade conditions at public transit stops.**

Shade-GIS supports the collection, review, analysis, and publication of structured transit-stop shade data. It is designed for researchers, transit agencies, municipalities, and community organizations that need a reproducible workflow for studying shade and related environmental conditions without developing a custom data-management application for each study.

The platform provides tools for importing transit-stop data, defining study-specific coding schemes, collecting and reviewing observations, measuring inter-rater agreement, visualizing results, and publishing interactive study applications.

## Why Shade-GIS?

Studies of transit-stop shade can require several distinct stages: assembling stop locations, reviewing imagery or field observations, applying a coding protocol, resolving disagreements between reviewers, documenting methodology, analyzing results, and communicating findings.

These tasks are often implemented independently for each study.

Shade-GIS provides a reusable workflow for:

- importing transit-stop and geospatial data;
- defining operational terminology and coding taxonomies;
- collecting and reviewing structured observations;
- conducting independent inter-rater coding;
- calculating agreement statistics;
- maintaining raw observations and review history;
- identifying common data-quality problems;
- visualizing study results;
- exporting analysis-ready datasets; and
- publishing interactive Streamlit applications.

The objective is not to prescribe a single definition of shade or a single study design. Researchers can configure terminology, classifications, methodology, visualization settings, and additional dataset attributes for their own research questions.

## Research Applications

Shade-GIS can support projects such as:

- citywide or corridor-level inventories of transit-stop shade;
- assessments of bus shelters and other passenger infrastructure;
- comparisons of shade conditions with ridership, routes, destinations, or neighborhood characteristics;
- community-based transit infrastructure audits;
- inter-rater reliability studies using imagery or field observations; and
- public-facing maps communicating environmental conditions at transit stops.

Although Shade-GIS was developed around transit-stop shade research, imported datasets can retain additional project-specific attributes for use in maps, filters, visualizations, and exports.

## Study Workflow

A typical Shade-GIS project follows four stages.

### 1. Prepare the Dataset

Researchers import stop locations from GTFS, CSV, GeoJSON, Shapefiles, API-hosted data, or manual records.

Shade-GIS preserves the core stop identifiers and coordinates required by the platform while retaining additional project-specific fields.

Before publication, the platform checks for common problems including:

- duplicate stop identifiers;
- missing coordinates;
- missing required fields;
- invalid point geometries; and
- images that cannot be associated with a stop.

### 2. Define the Coding Protocol

Researchers configure the terminology and classifications used by the study.

For shade studies, Shade-GIS distinguishes between two concepts:

- **Shade source** — what produces the visible shade.
- **Shade coverage** — how much of the passenger waiting area is visibly shaded.

Definitions and display labels can be adapted to the research design while internal codes remain stable.

### 3. Collect and Review Observations

Observations are stored separately from final reviewed classifications.

The standard review workflow allows project administrators to inspect submissions, resolve disagreements, assign final classifications, and retain an audit history of review decisions.

For studies requiring formal reliability assessment, Shade-GIS also provides an independent inter-rater workflow. Researchers can define a versioned codebook, assign assessment units to reviewers, collect locked independent ratings, calculate agreement statistics, and adjudicate disagreements without modifying the original ratings.

### 4. Analyze and Publish

Reviewed datasets can be explored through maps, summary statistics, configurable visualizations, and data exports.

Shade-GIS can also generate a standalone Streamlit application containing the current study dataset, methodology, configuration, visualizations, and downloadable data.

Public community observations can optionally be collected separately from the researcher-reviewed dataset.

## Inter-Rater Reliability

Shade-GIS includes an optional workflow for studies that require independent coding.

A protocol can use either images or transit stops as its assessment unit.

In **image mode**, evidence images receive randomized identifiers such as `IMG-######`, and stop or geographic information is withheld where possible.

In **stop mode**, reviewers evaluate individual stops under randomized identifiers such as `STOP-######`. This mode can support field surveys, project imagery, or external imagery sources where location information is inherently available.

During independent coding, reviewers cannot see:

- existing classifications;
- other reviewers' responses;
- reviewer comments;
- aggregate results; or
- adjudication outcomes.

Each assignment accepts one immutable submission and records the evidence method used.

Agreement statistics are withheld until coding is complete. Shade-GIS then reports pairwise percent agreement and Krippendorff's alpha for each codebook variable. Variables can use different measurement levels; the bundled shade protocol, for example, treats coverage as ordinal and source classifications as nominal.

Cases falling below a prespecified agreement threshold can be sent to adjudication. Adjudicated values are stored separately from the original ratings so the raw reliability data remain available for analysis.

Researchers can export the protocol, assignments, raw ratings, evidence methods, agreement results, and adjudication information for independent statistical analysis.

## Example Dataset

Shade-GIS includes a small example study using transit stops from the Hillsborough Area Regional Transit (HART) system in Tampa, Florida.

The dataset is intended to demonstrate the research workflow rather than serve as a complete inventory of shade conditions.

The bundled example contains 34 manually reviewed bus-stop observations coded using Google Maps imagery. The passenger waiting area is used as the unit of analysis.

### Waiting Area

The example study defines the **waiting area** as:

> The designated location where passengers would reasonably stand or sit while waiting to board the bus, including a bus stop pad, sidewalk immediately adjacent to the bus stop sign, or seating within a bus shelter.

Grass, landscaping, roadways, bicycle lanes, and areas not reasonably intended for passenger waiting are excluded.

### Shade Source

The example protocol distinguishes three sources of shade:

| Category | Operational definition |
| --- | --- |
| Natural | Trees, palms, hedges, or other vegetation visibly shade the waiting area. |
| Purpose-built | A designated bus shelter, awning, canopy, overhang, or similar passenger shelter visibly shades the waiting area. |
| Incidental | A nearby building or other non-shelter built feature visibly shades the waiting area. |

Multiple sources may be recorded when appropriate.

### Shade Coverage

| Category | Operational definition |
| --- | --- |
| No Shade | No shade visibly reaches the waiting area. |
| Limited Shade | Shade visibly reaches part of the waiting area but does not cover most of it. |
| Significant Shade | Shade visibly covers most of the waiting area or seating area. |

The example's `shading` variable is derived from the shade-coverage classification for visualization and filtering.

### Limitations of the Example Data

The example dataset was coded from Google Maps imagery rather than contemporaneous field measurements.

Consequently, observed shade can be affected by:

- imagery date;
- season;
- time of day;
- camera position;
- temporary obstructions; and
- availability of street-level imagery.

The example should therefore be treated as a demonstration of the Shade-GIS workflow rather than a definitive assessment of current shade conditions at the included stops.

Researchers conducting substantive studies should define an appropriate evidence protocol and independently review their study data.

## Supported Data

At minimum, each stop requires:

| Field | Description |
| --- | --- |
| `stop_id` | Unique stop identifier, typically derived from GTFS. |
| `stop_name` | Public stop name. |
| `stop_lat` | Latitude in WGS84. |
| `stop_lon` | Longitude in WGS84. |

Shade-GIS also recognizes optional fields such as:

`agency`, `routes`, `municipality`, `shading`, `shade_coverage`, `shade_sources`, `review_status`, `confidence`, `ridership`, and `nearby_destinations`.

Additional imported columns are preserved as project attributes and can be used in supported displays, map hovers, filters, visualizations, exports, and GIS overlays.

### Import Formats

| Source | Support |
| --- | --- |
| GTFS ZIP | Imports `stops.txt` and can enrich stops using routes and trip information. |
| CSV / `stops.txt` | Supports mapping source columns to the Shade-GIS schema. |
| GeoJSON | Supports features and derives an on-geometry representative point for non-point features. |
| Zipped Shapefile | Imports records through the standard field-mapping workflow and reprojects a matching `.prj` coordinate system to WGS84. |
| API URL | Imports CSV or GeoJSON from supported HTTP(S) sources. |
| Manual entry | Allows individual stop records to be entered directly. |

## Architecture

Shade-GIS consists of three primary components:

### Builder

The research interface used to create projects, import data, configure coding protocols, review observations, inspect results, and prepare publications.

### Project Store

A persistent SQLite-backed store used for local projects. It records project metadata, taxonomy, methodology, visualization settings, stops, imports, observations, and review history.

A PostgreSQL schema is also provided for researchers who require shared or hosted deployments.

### Published Study Application

A standalone Streamlit application generated from a Shade-GIS project. Published applications can contain the study dataset, methodology, maps, visualizations, downloads, and optional community observations.

The Python implementation is organized under `shade_gis/`, while deployment functionality is maintained under `shade_gis/deploy/`.

## Getting Started

### Requirements

- Python 3.11 or newer is recommended.
- A local shell capable of running Python and Streamlit.
- Docker and PostgreSQL are optional for testing shared-database deployments.

### Installation

Clone the repository:

```bash
git clone https://github.com/OWNER/REPO.git
cd Shade-GIS
```

Install the runtime dependencies:

```bash
pip install -r requirements/requirements.txt
```

Start Shade-GIS:

```bash
streamlit run app.py
```

The application opens with the bundled example project. Researchers can also create a new project and import their own dataset.

### Local Storage

Shade-GIS uses SQLite by default.

On Windows, the preferred database location is:

```text
%LOCALAPPDATA%\Shade-GIS\shade_study_builder.sqlite3
```

On other systems, Shade-GIS attempts to use:

```text
platform_data/shade_study_builder.sqlite3
```

A custom writable database path can be supplied with:

```text
SHADE_GIS_DB_PATH
```

## Repository Structure

```text
Shade-GIS/
|-- app.py
|-- builder_app.py
|-- published_app.py
|-- shade_gis/
|   |-- deploy/
|   `-- pages/
|-- docs/
|-- sql/
|-- scripts/
|-- tests/
|-- requirements/
`-- platform_data/
```

`app.py` is the local research-builder entry point.

`shade_gis/` contains the primary application and domain logic.

`shade_gis/deploy/` contains deployment and bundle-generation functionality.

`docs/` contains supporting technical and methodological documentation.

`sql/` contains the optional PostgreSQL schema.

`tests/` contains automated tests for platform behavior.

## Testing

Install the test dependencies:

```bash
pip install -r requirements/requirements-test.txt
```

Run the standard test suite:

```bash
pytest -q
```

The standard suite uses temporary databases and fixtures and does not modify the researcher's local Shade-GIS database.

Browser-based tests are maintained separately:

```bash
pip install -r requirements/requirements-ui.txt
python -m playwright install chromium
pytest -q -m ui
```

The default test run excludes tests marked `ui`.

## Publishing a Study

The builder's **Deploy** workflow creates a standalone Streamlit application from the active project.

Before publication, Shade-GIS checks the dataset for blocking data-quality problems. A generated study contains a snapshot of the active dataset and its associated research configuration.

Depending on project settings, the generated application can include:

- the reviewed stop dataset;
- raw observations;
- study metadata;
- methodology;
- maps and visualizations;
- downloadable datasets; and
- optional community observations.

Researchers can publish through the integrated GitHub workflow or download the generated application package for manual deployment.

The local builder and generated public application are intentionally separate: `app.py` at the repository root is the research administration interface and should not be exposed as the public study application.

See the deployment documentation for database configuration, persistent community-observation storage, authentication options, and production security guidance.

## Community Observations

Shade-GIS optionally supports public contributions to a published study.

Community observations are maintained separately from the researcher-reviewed dataset. Public input therefore does not automatically modify an administrator-approved classification.

Projects can configure:

- whether community contributions are enabled;
- which classifications visitors may submit;
- explanatory text presented to contributors;
- whether authentication is required;
- whether contributors may revise submissions; and
- thresholds used when displaying community consensus.

Anonymous public participation has inherent limitations. Shade-GIS includes basic abuse-resistance mechanisms, but studies in which voting outcomes have substantive consequences should use stronger authentication and appropriate external protections.

Detailed deployment and security considerations are documented separately.

## PostgreSQL

SQLite is sufficient for local research workflows.

Researchers conducting shared or hosted studies can use the PostgreSQL schema provided under `sql/`.

To start the development database:

```bash
docker-compose up -d
```

Install the optional database dependencies:

```bash
pip install -r requirements/requirements-db.txt
```

Initialize the schema:

```bash
python scripts/init_db.py
```

This command applies `sql/schema.sql` and every ordered migration under `sql/migrations/`, so it also upgrades existing databases before loading the seed project.

The PostgreSQL model supports projects, stops, images, observations, releases, review history, taxonomy, settings, and import provenance.

Production database configuration and security guidance are documented separately.

## Import Safety

Shade-GIS applies configurable limits to uploaded files, archive contents, and remote API responses.

The default limits are:

```text
File and overlay uploads: 50 MB
API responses: 15 MB
ZIP members: 256
ZIP member size: 80 MB
ZIP total uncompressed size: 150 MB
```

Remote imports must use HTTP or HTTPS and cannot contain embedded credentials. Localhost and private-network targets are blocked by default.

These controls can be configured for research environments that intentionally use private data services.

See the technical documentation for the corresponding environment variables and deployment guidance.

## Reproducibility and Research Use

Shade-GIS is intended to make the process used to produce a study dataset more explicit and reproducible.

Researchers remain responsible for defining an appropriate study design, sampling strategy, evidence source, coding protocol, reliability procedure, and interpretation of results.

In particular, a Shade-GIS classification represents the observation produced under the protocol configured by the researcher. It should not automatically be interpreted as a direct physical measurement of thermal comfort, solar exposure, temperature, or heat risk.

Researchers publishing results produced with Shade-GIS should document:

- the source and date of the underlying transit data;
- the study's unit of analysis;
- the evidence source used for coding;
- operational definitions and classification rules;
- reviewer recruitment or assignment procedures;
- inter-rater reliability procedures, where applicable;
- adjudication procedures;
- known limitations of imagery or field observations; and
- any transformations applied before analysis.

## Documentation

Additional documentation is available under `docs/`, including:

- platform schema documentation;
- data-quality checks;
- deployment guidance;
- database configuration;
- contribution guidelines; and
- project governance.

The repository changelog records significant changes to the platform.

## License

Shade-GIS is released under the MIT License.

See `LICENSE` for the terms governing reuse and distribution.

## Citation

Citation metadata are provided in `CITATION.cff`.

Researchers who use Shade-GIS in published work are encouraged to cite the software version used for their analysis and, where appropriate, archive the corresponding study configuration and dataset release.

## Contributing and Support

Bug reports, research-use feedback, and contributions are welcome.

See:

- `CONTRIBUTING.md` for the contribution workflow;
- `SUPPORT.md` for support expectations; and
- `GOVERNANCE.md` for project governance.
