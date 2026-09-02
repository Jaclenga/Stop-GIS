# Data quality workflow

[Documentation home](README.md) · [UX guide](user-experience.md)

The dedicated **Data Quality** page under **Dataset → Quality** is the final validation surface for the
active project dataset. Open it after importing or replacing stops. The dashboard runs
the same report whenever Streamlit reruns, so its counts, affected-record viewer, and publication
readiness banner always describe the current project state.

## Validation checks

| Check | Affected records | Resolution |
| --- | --- | --- |
| Duplicate stop IDs | Every stop row whose trimmed `stop_id` occurs more than once. | Correct the IDs in the source dataset and reimport it. Each stop ID must identify one stop. |
| Missing coordinates | Stop rows with a blank latitude, longitude, or both. | Supply both WGS84 coordinates and reimport the corrected row. |
| Missing required fields | Stop rows missing `stop_id`, `stop_name`, `stop_lat`, or `stop_lon`. | Fill every listed required field in the source or field mapping and reimport. |
| Invalid geometries | Stop rows with nonnumeric coordinates, latitude outside `-90` to `90`, or longitude outside `-180` to `180`. | Correct the point coordinates in the source dataset. GeoJSON and Shapefile inputs are represented by their derived stop point after import. |

A record can fail more than one check. For example, a stop with blank coordinates appears under both
**Missing coordinates** and **Missing required fields**. The readiness total therefore counts affected
record occurrences, while each validation row shows the count for that specific rule.

## Review affected records

1. Select **Dataset**, then **Quality** in the second-level navigation.
2. Select **View affected records** beside a failing check. This sets the validation filter and opens
   the corresponding stop rows in the affected-record table below the summary.
3. Alternatively, choose a rule from **Filter dataset by validation issue**. Choose **All validation
   issues** to view the compact cross-rule findings table.
4. Use the displayed source row, record ID, and quality details to correct the source data, then
   reimport or save the corrected project state.

The viewer is paginated so large feeds do not mount every failing row in the browser at once.

## Publication readiness

The banner reports **Publication-ready** only when:

- the active dataset contains at least one stop; and
- all four active validation checks have zero affected records.

The **Publish** page and deployment bundle builder enforce this result. A dataset with any blocking
finding cannot be packaged or published; **Open Data Quality** returns to the affected-record review
workflow so the source records can be corrected first. This guard is evaluated again for updates to
an already-published study, so an earlier successful deployment does not bypass current data checks.

This summary covers structural data readiness. Label completion, disagreement resolution, methodology,
source licensing, and release decisions remain visible in their respective builder workflows and may
still need review before a team publishes a study.

Import normalization continues to reject blank stop IDs, missing coordinates, and duplicate IDs when
building the active stop table. The Data Quality dashboard is the authoritative final check for the
resulting active dataset, including records loaded from older projects or changed outside the current
import session. Image records remain compatible with existing databases but are not part of the
active MVP readiness workflow.
