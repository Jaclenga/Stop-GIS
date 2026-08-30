# Migrating existing projects to Stop-GIS

[Documentation home](README.md) · [Platform schema](platform_schema.md)

The upgrade is additive and runs when the SQLite store is initialized.

1. Stop-GIS accepts `STOP_GIS_DB_PATH` first and the former database-path variable as a fallback.
2. If the new default database does not exist but the former default does, Stop-GIS opens and upgrades the existing file in place.
3. The migration adds `project_settings.scoring_json`, `assessment_modes`, and `assessments`.
4. Every project without mode definitions receives enabled `shade_coverage` and `shade_source` definitions derived from its existing taxonomy.
5. Existing stop shade values, labels, voting records, blind ratings, adjudications, review history, images, releases, and import logs are not deleted or rewritten.
6. Assessment writes advance the project revision, and database triggers reject cross-project or cross-stop adjudication links.

PostgreSQL operators should back up the database and apply
`sql/migrations/003_stop_gis_assessment_modes.sql` with the same migration role used for earlier schema changes.
Deployments that applied an earlier draft of migration 003 should safely run the current idempotent
migration again to install the adjudication-lineage trigger.

## Intentionally retained identifiers

The following names remain compatibility contracts rather than public product branding:

- the `shade_gis` import package, while new imports use the `stop_gis` namespace facade;
- `shade_taxonomy`, `shade_labels`, `shade_votes`, and their shade-specific columns;
- former environment-variable names, which remain fallback aliases;
- `.shade_gis_votes.sqlite3`, `shade_study_*.csv/json`, and `static/shade_gis_identity.json` in existing generated deployments;
- the Android application ID and its on-device observation/backup filenames.

Renaming those values automatically would risk orphaning user databases, deployment ownership
records, configured secrets, or mobile evidence. They may be retired only in a future major
migration with explicit backup, conversion, and rollback tooling.
