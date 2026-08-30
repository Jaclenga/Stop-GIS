# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this repository is currently in a
pre-release phase while the reusable platform stabilizes.

## [Unreleased]

### Added
- A task-oriented documentation hub and user-experience guide covering
  navigation, autosave feedback, confirmations, filter recovery, responsive
  behavior, public study semantics, and community-voting validity safeguards.
- Independent inter-rater coding by standardized image or transit stop, with versioned protocols,
  randomized neutral assignments, bundled stop evidence, recorded field/map/image review methods,
  immutable multi-field ratings, phase-gated agreement, adjudication, and research exports.
- Editable project terminology, seeded with the `Waiting Area` operational definition and carried
  through builder labeling, published methodology, exports, documentation, and starter previews.
- Inline editors for Shade Source and Shade Coverage display labels and operational definitions,
  with hidden stable category codes, per-taxonomy definition reset actions, and project-specific
  wording propagated to labeling and voting.
- Centralized Data Quality dashboard with record-level filters for duplicate stop IDs, missing
  coordinates, missing required fields, invalid geometries, orphaned images, and publication readiness.
- Compact Export Files catalog with descriptions, record counts, generated sizes, data dates,
  per-row downloads, and a separate Dataset Provenance section.

### Changed
- Streamlined safe navigation, standardized publishing terminology, added
  accessible autosave feedback and filter recovery actions, made project cards
  content-responsive, and added confirmation before GIS overlay removal.
- Public voting now requires an explicit complete response and presents
  user-focused storage failure recovery without backend implementation details.
- Clarified Labeling information architecture: Dataset Review now leads with moderation, Intercoder Review
  follows Study Setup → Review Materials → Study Progress, and Community Voting pairs grouped configuration
  with a live preview while moving versioning, role preview, and deployment guidance into disclosures.
- Reorganized Labeling into `Dataset Review`, `Intercoder Review`, and `Community Voting`, with separate
  submit-label, review-queue, and audit-history views and clearer moderator-facing queue copy.
- Redesigned the terminology and taxonomy workspace as a centered, research-oriented card layout
  with lighter tables, roomier wrapped definitions, sentence-case headings, and compact inline actions.
- Moved terminology and taxonomy editing from Data Overview to a dedicated Taxonomy page in
  Dataset's second-level navigation.
- Deploy is now a nontechnical publishing wizard with automatic repository and branch detection,
  one-click packaging and repository updates, four plain-language stages, website verification,
  success/update/unpublish actions, and the former ZIP/PowerShell workflow retained as an advanced fallback.
- Builder Docs and public methodology taxonomy tables keep configured category ordering without
  displaying the internal `sort_order` field.
- Progress-oriented Dataset Status dashboard with coverage/review progress bars, a direct handoff
  to Dataset Review, and a collapsed paginated Dataset Preview replacing the former Dataset Health spreadsheets.
- Consolidated moderator review, disagreement resolution, and audit history in Dataset Review; Preview
  agreement analytics are read-only and no longer expose a second write-capable adjudication flow.
- Removed duplicate chart, table, methodology, and voting preview implementations. Visuals keeps a
  live configuration map, Docs is editor-only, and Community Voting reuses the production voting panel.
- Extracted shared taxonomy, data-quality, and table components from page modules and removed
  wildcard page imports from the builder application.
- Configurable public bus-stop coverage voting in generated apps, including editable admin controls,
  per-browser-session vote handling, consensus thresholds, SQLite development storage, and optional
  shared PostgreSQL persistence for hosted deployments, managed from a dedicated builder page.
- Canonical separation of shade coverage (`No Shade`, `Limited Shade`, `Significant Shade`) from
  shade sources (`Natural`, `Purpose-built`, `Incidental`) across imports, labeling, voting, maps, and exports.
- Public voting now mirrors raw labeling with a separate multi-checkbox shade-source assessment
  persisted alongside each coverage vote.
- Public contributor guidance in `CONTRIBUTING.md`.
- Support expectations in `SUPPORT.md`.
- Governance and maintainer decision guidance in `GOVERNANCE.md`.
- Automated test and CI documentation in `README.md`.

## [0.1.0] - 2026-07-01

### Added
- Reusable Stop-GIS Builder workflow for importing GTFS, CSV, GeoJSON,
  zipped Shapefile, API-hosted, and manual stop data.
- Multi-project local SQLite storage with schema notes for shared Postgres
  deployments.
- Public preview app generation, export tools, raw labeling workflow, admin
  review tools, and visualization controls.
- Pytest coverage for storage, imports, labels, exports, scoring, smoke tests,
  and Playwright-based UI coverage in CI.
