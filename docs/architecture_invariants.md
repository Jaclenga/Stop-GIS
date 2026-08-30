# Stop-GIS architecture invariants

This audit replaces repeated edge-case scans with a small set of boundaries and
state transitions that must remain true across the builder, published app,
persistence layer, deployment workflow, and Android client.

## Root-cause map

| Recurring bug family | Root cause | Canonical boundary | Enforced invariant |
| --- | --- | --- | --- |
| Mixed stop identifiers | Consumers independently called `str()` or `astype(str)` | `stop_gis.identifiers.canonical_identifier` at import and analytics boundaries | Scalar representations of the same numeric ID compare equally; textual leading zeros remain meaningful |
| Mode-schema drift | Amenities were represented as ad hoc permanent columns | `stop_gis.assessment_modes` validation plus `assessment_modes` persistence | A mode key has one validated definition per project; arbitrary new modes require no schema column |
| Lost independent ratings | Current reviewed values overwrote raw submissions | Append-only `assessments` plus separate current stop projection | Independent ratings and adjudications remain reproducible after the current map value changes |
| Nested classification values | Imported tabular values were assumed to be scalar | Scalar validation in `builder_imports` classification normalizers | Lists, arrays, and mappings become review-safe fallback values instead of reaching scalar-only pandas operations |
| Builder/published analytics drift | Standalone published code duplicates analytical behavior | Cross-implementation invariant tests | Majority, reliability, and disagreement results remain equivalent for the same normalized data |
| Stale deployment success | Repository identity and UI state were not tied to all published content | Release fingerprint plus target-and-bundle result key | Any hosted-runtime change invalidates verification; any target or bundle change invalidates cached success |
| Review decisions hiding newer evidence | Status was treated as timeless state | Resolution timestamp compared with latest evidence timestamp | Resolution hides a disagreement only until newer evidence arrives |
| Invalid or unsafe exports | Serialization happened directly at download call sites | Published serialization helpers, reused by builder pages | Nested properties are JSON-safe, geometries are finite/in-range, and spreadsheet formulas are neutralized |
| Mixed persistence revisions | Related reads relied on implicit SQLite behavior | Explicit read transaction in `load_project_bundle` | One returned bundle represents one database snapshot |
| Blind-coding race/statistic drift | Intent was read before locking; unequal unit sizes were weighted incorrectly | Locked protocol comparison and coincidence-normalized reliability | Assessment-unit changes abort stale assignment requests; each unit contributes correctly to alpha |
| Mobile evidence deletion | Cleanup inferred ownership only from saved observations | Protected draft URIs plus legacy-backup URI inventory | Active drafts and backed-up legacy evidence are never orphan-cleaned |

## State-transition invariants

### Deployment

`bundle built -> validated -> published -> verified`

- Verification is valid only for the exact release fingerprint.
- Every generated bundle validates its own schema and recomputed release
  fingerprint before it can be published; legacy bundles without a release
  fingerprint retain schema-1 compatibility.
- UI success is valid only for the exact repository, branch, mode, public URL,
  and bundle ID.
- A new bundle or target returns the workflow to an unverified state.

### Review

`disagreement -> resolved(t1) -> new evidence(t2) -> disagreement -> resolved(t3)`

- A resolution hides the queue item when `resolution_time >= latest_label_time`.
- Newer evidence reopens it.
- Legacy terminal status without a resolution timestamp cannot silently hide a
  current disagreement.

### Persistence

`loaded revision A -> saved revision B`

- Every component of a load comes from one SQLite snapshot.
- A writer holding stale revision A cannot overwrite revision B.
- New child evidence prevents deletion by a stale parent snapshot.

### Mobile evidence

`draft photo -> active draft -> saved observation`

- Activity/process recreation supplies active draft URIs to cleanup.
- A skipped legacy record is preserved in the legacy backup, and every photo URI
  referenced by that backup remains protected.

## Test strategy

`tests/test_architecture_invariants.py` uses deterministic generated cases rather
than one-example regressions. Focused state tests remain beside their owning
modules. New bug reports should first be mapped to an invariant above; a new
one-off test is warranted only when no existing invariant can express the
failure.
