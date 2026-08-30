# Stop-GIS

[![Tests](https://github.com/Jaclenga/Stop-GIS/actions/workflows/tests.yml/badge.svg)](https://github.com/Jaclenga/Stop-GIS/actions/workflows/tests.yml)

Stop-GIS is an open-source platform for reproducible audits of public-transit
stop infrastructure, amenities, accessibility, and passenger comfort.

Researchers, transit agencies, municipalities, and community organizations can
configure a study, import stops, collect independent observations, review
disagreements, analyze results, and publish a versioned Streamlit website
without building a new data application for every audit.

## Start here

- [Install and run Stop-GIS](#quick-start)
- [Understand the product workflow](#workflow)
- [Browse all documentation](docs/README.md)
- [Review the user-experience guidelines](docs/user-experience.md)
- [Contribute to the project](docs/CONTRIBUTING.md)
- [Get support](docs/project/SUPPORT.md)

## What Stop-GIS assesses

Researchers define and include only the coding dimensions needed by a project.
Starter definitions cover:

- stop amenities such as benches, shelters, lighting, trash cans, and passenger
  information;
- accessibility features such as boarding pads, sidewalks, curb ramps,
  crosswalks, and wheelchair access;
- passenger-comfort factors such as shade, cleanliness, and traffic exposure;
- custom categorical, multi-select, boolean, numeric, or text observations.

Each dimension has a stable key, display label, operational definition, value
type, researcher-defined allowed values, collection settings, and visibility
rules. New dimensions do not require source-code changes or new database
columns. Exports include analysis-ready dimension columns and a machine-readable
schema/codebook.

### Starter templates

| Template | Initial focus |
| --- | --- |
| Basic Stop Amenities | Benches, shelters, trash cans, lighting, and passenger information |
| Accessibility Audit | Sidewalks, boarding pads, wheelchair access, curb ramps, and crosswalks |
| Passenger Comfort | Amenities, shade, lighting, cleanliness, and traffic exposure |
| Custom | A project-specific selection from the assessment catalog |

Templates are starting points. Teams can change labels, definitions, allowed
values, requirements, ordering, display surfaces, and scoring.

## Workflow

1. **Create** a project and select a template or define coding dimensions.
2. **Import** stops from GTFS, CSV, GeoJSON, Shapefile, an HTTP API, or manual
   entry. Arbitrary imported attributes are preserved.
3. **Collect** immutable reviewer submissions with evidence method, comments,
   confidence, timestamps, and reviewer identity.
4. **Review** conflicts while retaining independent observations and appending
   adjudications and audit-history events.
5. **Analyze** included dimensions on maps, filters, tables, and dashboards.
6. **Publish** a standalone, versioned study website or download its data.

Stop-GIS automatically saves the active project, validates publication data,
requires confirmation for destructive workflow changes, and provides recovery
actions for filtered empty states. See the
[user-experience guide](docs/user-experience.md) for the complete interaction
model and accessibility expectations.

## Quick start

Python 3.11 or newer is recommended.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements/requirements.txt
streamlit run stop_gis/entrypoints/builder.py
```

Run the automated tests with:

```powershell
pytest -q -m "not ui"
pytest -q -m ui
```

The UI suite requires the packages in `requirements/requirements-ui.txt` and a
Playwright Chromium installation. See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for the
complete development setup.

## Configuration

The primary local database variable is `STOP_GIS_DB_PATH`. If it is unset,
Stop-GIS discovers an existing compatible database or uses:

```text
%LOCALAPPDATA%\Stop-GIS\stop_gis_builder.sqlite3
```

Import limits use the `STOP_GIS_MAX_*` variables. API access can be constrained
with `STOP_GIS_ALLOWED_API_HOSTS` and `STOP_GIS_ALLOW_PRIVATE_API_URLS`.
Hosted voting uses `STOP_GIS_VOTE_DATABASE_URL`,
`STOP_GIS_VOTE_FINGERPRINT_SECRET`, and related deployment settings documented
in the generated deployment package.

Former `SHADE_GIS_*` variables remain fallback aliases for compatibility. See
[the migration guide](docs/migrating-from-shade-gis.md) before changing an
existing installation.

## Architecture at a glance

- `assessment_modes` stores each project's validated mode definitions.
- `assessments` stores immutable submissions and per-mode evidence.
- each stop keeps a separate current reviewed projection for maps and exports.
- `review_history` retains moderator decisions.
- optional scoring profiles live in project settings and travel with exports.

Independent and adjudicated observations remain distinct. Reliability metrics
use independent submissions only and respect each mode's measurement level.
Optional scores are study-specific analytical constructs, not universal
measures of stop quality.

Shade remains a first-class compatibility subject through coordinated coverage
and source modes. Existing shade data, voting deployments, databases, and
internal identifiers are upgraded additively rather than renamed in place.

### Repository layout

```text
clients/android/            Android field-collection client
examples/published-site/    Tracked standalone-publication example
stop_gis/
  entrypoints/              Builder and published-app launchers
  builder/app.py            Builder composition
  builder/                  Import, labeling, and visualization services
  domain/                   Taxonomy, identifiers, quality, and coding rules
  ui/                       Reusable Streamlit components
  pages/                    Builder page composition
  deploy/                   Bundle generation and publishing services
  persistence/              Project storage adapters
  public_app.py             Shared published-study runtime
  public_voting.py          Shared public voting runtime
data/demo/                  Versioned demonstration inputs
scripts/                    Maintenance and data-preparation commands
tests/                      Unit, integration, architecture, and UI tests
docs/                       User and maintainer documentation
infrastructure/             Compose and database schema/migrations
```

Application code uses the `stop_gis` namespace throughout. Persisted legacy
identifiers remain supported where changing them could orphan existing project
data, but the retired `shade_gis` source package is no longer present.

## Documentation

The [documentation hub](docs/README.md) organizes guides by task and audience.
Key references include:

- [User experience and accessibility](docs/user-experience.md)
- [Data quality workflow](docs/data_quality.md)
- [Platform schema](docs/platform_schema.md)
- [Architecture invariants](docs/architecture_invariants.md)
- [Shade-GIS migration](docs/migrating-from-shade-gis.md)
- [Android field client](clients/android/README.md)
- [AI use statement](docs/project/AI_USE.md)

## Demo data

The bundled Tampa/HART inputs live in [`data/demo/`](data/demo/). This is a
small workflow demonstration; its amenity observations are example data, not a
complete or current HART inventory.

## License

Stop-GIS is released under the [MIT License](LICENSE).

## Citation

Cite the software version and archive the study configuration, assessment
definitions, raw observations, scoring profiles, and exported dataset used in
an analysis. Citation metadata is available in [CITATION.cff](CITATION.cff).
