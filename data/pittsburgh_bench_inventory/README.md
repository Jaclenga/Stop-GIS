# Pittsburgh bus-stop bench-inventory starter dataset

This directory contains a reproducible, **unreviewed starter dataset** for a
City of Pittsburgh bus-stop seating study in Stop-GIS. Its unit of analysis is
one current Pittsburgh Regional Transit (PRT) `stop_id`, limited to records
whose official PRT attributes say `mode = 'BUS'` and
`muni = 'Pittsburgh city (Allegheny, PA)'`.

This is not a verified bench census. No complete, authoritative public bench
census for Pittsburgh bus stops was located. The source columns are leads for
human review, not Stop-GIS assessments:

- PRT's official current-stop frame supplies the authoritative stop IDs,
  public stop codes, locations, service, and geography, but has no bench field.
- PRT's public current-amenities layer lists shelters and selected station
  amenities. It does not expose benches as a distinct amenity type. A value of
  `prt_shelter_listed=no` means only that the build found no matching public
  shelter-layer record; it is not independently verified absence. A shelter
  listing never implies that a bench is present.
- OpenStreetMap (OSM) supports tags such as `bench=yes` and `bench=no`, but OSM
  is contributor-maintained, incomplete, and potentially stale. The `osm_*`
  values are provisional source evidence only.
- [Pittsburghers for Public Transit collects built-in and DIY seating
  information](https://www.pittsburghforpublictransit.org/crowdsourced-bus-stop-form/),
  but no public results download was located for use in this build.

Every `bench_review_status` is therefore `unreviewed`. The CSV includes a
`bench` input column mapped to Stop-GIS's `bench_presence` mode: exact-code OSM
`bench=yes` tags become `present`, `bench=no` tags become `absent`, and missing
or unsupported tags remain blank. These are editable reviewer prefills and do
not change the stop's unreviewed status or create an adjudicated observation;
the original `osm_bench_tag` and provenance fields remain alongside them.

## Files

- `pittsburgh_bus_stops_stop_gis_import.csv` — Stop-GIS-ready source frame.
- `build_pittsburgh_bench_seed.py` — reproducible downloader, matcher, builder,
  and validator.
- `build_summary.json` — access timestamp, source metadata, current counts,
  matching rules, artifact digest, validation results, and limitations.
- `DATA_LICENSE.md` — source attribution and reuse obligations.
- `CITATION.md` — ready-to-use citations, access dates, and attribution text.
- `CITATION.cff` — machine-readable citation metadata for data repositories.
- `stop_gis_project.json` — portable Pittsburgh bench-study defaults, field
  mapping, input dimensions, source-prefill rules, and map colors.
- `Pittsburgh_Stop_GIS_Bench_Starter.zip` — the CSV, builder, summary, README,
  license/attribution notice, citation files, and project-default manifest.

## Citation and attribution

Cite the derived dataset and all source datasets using `CITATION.md` (or import
`CITATION.cff` into a compatible reference manager). Preserve
`DATA_LICENSE.md` with every redistributed copy: it contains the full notice
required by PRT's Developer License Agreement and the OpenStreetMap attribution.
The ArcGIS metadata and the governing PRT terms are recorded separately so the
catalog's `CC0` value is not mistaken for the only applicable condition.

The complete ZIP can be uploaded directly in Stop-GIS as a CSV dataset
package. A fresh Stop-GIS installation also creates this Pittsburgh project
automatically; no upload is needed for that path.

## Rebuild

From the repository root, using Python 3.11 or newer and the Stop-GIS project
dependencies:

```powershell
python data/pittsburgh_bench_inventory/build_pittsburgh_bench_seed.py
```

The builder retrieves all ArcGIS pages in stable `OBJECTID` order, checks the
record count before and after pagination, queries Overpass, rebuilds the CSV
and summary, validates the Stop-GIS import path, and recreates the ZIP. It
requires internet access. Source data can change, so do not expect historical
counts to remain fixed.

## Source matching

PRT shelter rows are joined primarily on exact `stop_id`; normalized public
`stop_code` is used only as a fallback. Multiple shelter records are combined
without inferring bench presence.

OSM elements are deduplicated by element type and ID. Their `ref` tags are
split on commas and semicolons and compared with normalized PRT public stop
codes. When several OSM elements contain the same code, the geographically
closest is selected. The builder never matches by proximity alone:

- zero through 50 m: `exact_stop_code`;
- over 50 m through 250 m: `exact_stop_code_large_offset`;
- over 250 m: rejected and represented as `unmatched`;
- no exact code candidate: `unmatched`.

Review `build_summary.json` for rejected-candidate and other current counts.

## Stop-GIS project setup

This inventory is the built-in project loaded by every fresh Stop-GIS project
store. Its default configuration is a **bench/seating study**, not a shade
study: legacy shade dimensions and unrelated infrastructure dimensions are not
enabled. For a manual import, keep the four automatically mapped required
fields (`stop_id`, `stop_name`, `stop_lat`, and `stop_lon`), map `routes` and
`municipality`, and preserve the remaining columns as imported attributes.
The default assessment configuration contains only these dimensions:

| Stable key | Label | Allowed values |
| --- | --- | --- |
| `bench_presence` | Bench presence (source column: `bench`) | `present`, `absent`, `unclear` |
| `bench_condition` | Bench condition | `usable`, `damaged`, `unusable`, `unclear`, `not_applicable` |
| `seating_form` | Seating form | `traditional_bench`, `shelter_integrated_bench`, `simme_seat`, `individual_seat`, `lean_rail`, `other`, `unclear` |
| `informal_seating` | Informal/DIY seating | `present`, `absent`, `unclear` |

The default map uses a colorblind-friendly palette for `bench_presence`: blue
marks mapped bench locations, vermillion marks mapped no-bench locations,
amber marks `unclear`, and gray marks stops without mapped bench evidence.
Reopening an older built-in
Pittsburgh project upgrades its saved map view and derives the same editable
prefills from retained `osm_bench_tag` values.

### Operational definition

- Count only furniture clearly intended to serve the passenger waiting area.
- Traditional and shelter-integrated benches qualify as benches.
- Simme-Seats, individual seats, and lean rails are seating but are not
  traditional benches.
- Loose chairs, milk crates, and improvised objects are informal seating.
- Do not count unrelated park benches, storefront furniture, landscaping
  walls, or seating whose relationship to the stop is ambiguous.
- Use `unclear` when imagery is missing, obstructed, stale, or ambiguous.

### Review order and reliability

Recommended review order:

1. Coordinate-offset matches (`exact_stop_code_large_offset`).
2. OSM `bench=yes` leads.
3. Official PRT shelter records.
4. Remaining stops by descending `weekday_trips`, while maintaining
   neighborhood coverage.

Double-code a stratified 10% sample of reviewed stops. Retain both reviewers'
raw, independent observations during adjudication instead of overwriting them.

## Validation performed by the builder

The build fails unless required Stop-GIS fields are populated, `stop_id` is
unique, coordinates are finite and within conservative Pittsburgh-area bounds,
all source rows remain in the requested PRT scope, source evidence retains
`prt_` or `osm_` labeling, no shade-study column is present in the source CSV,
and every record remains `unreviewed`. It also runs
the CSV through the repository's current `prepare_stop_dataset` function and
checks that all rows and required fields survive. Finally, it opens and tests
the ZIP and verifies its exact member list.

The generated `build_summary.json` and console output contain the current
counts and validation result. Source tags should still be reviewed against
current field observations or suitable imagery.
