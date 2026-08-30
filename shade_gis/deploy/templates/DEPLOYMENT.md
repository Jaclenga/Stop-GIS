# Bring your own voting database

Stop-GIS helps you configure voting infrastructure in your own provider account. Stop-GIS does
not create, own, centrally store, back up, or administer your research database. The generated app
connects directly from its server process to your database.

## Security boundary

- Put real credentials only in your application host's server-side secret manager.
- Never put a PostgreSQL URL in JavaScript, `shade_study_config.json`, source control, screenshots,
  support messages, or a downloaded deployment bundle.
- This package contains placeholders only. `.gitignore` excludes `.env`, real Streamlit secrets,
  SQLite files, and common credential filenames.
- Keep `STOP_GIS_VOTE_FINGERPRINT_SECRET` stable. Losing or rotating it changes anonymous voter
  pseudonyms and can weaken duplicate-vote continuity. It cannot be recovered by Stop-GIS.

## Recommended PostgreSQL roles

Use two roles when your provider supports them:

1. An owner or migration role that can create and alter the voting tables. Use it only for
   `scripts/migrate_database.py` or `migrations/001_public_voting.sql`.
2. A least-privilege runtime role used by the public app. It needs only `SELECT`, `INSERT`,
   `UPDATE`, and `DELETE` on `shade_votes`, `SELECT` on `shade_vote_settings`, and sequence usage.

Review `migrations/least_privilege_roles.sql.example`, replace `YOUR_DATABASE`, and apply it as an
administrator. Role creation and passwords are deliberately not automated.

## Streamlit Community Cloud walkthrough

1. Create a PostgreSQL 14+ project in [Neon](https://console.neon.tech/),
   [Supabase](https://supabase.com/dashboard/projects), or another provider. Require TLS with
   `sslmode=require`, `verify-ca`, or `verify-full`.
2. Download and unzip the Stop-GIS self-hosting package. Install dependencies with
   `python -m pip install -r requirements.txt`.
3. Set `STOP_GIS_VOTE_DATABASE_URL` temporarily in your local process to the owner-role URL, then
   run `python scripts/migrate_database.py`. The migration is transactional, idempotent, protected
   by a PostgreSQL advisory lock, and refuses to overwrite a newer schema version.
4. Apply `migrations/least_privilege_roles.sql.example`. Replace the environment value with the
   runtime-role URL and run `python scripts/verify_database.py`. Verification checks PostgreSQL
   version, schema version, and insert/read/update/delete access, then removes its probe record.
5. Generate a 32-byte fingerprint secret with the browser-only generator in the Stop-GIS Deploy
   page. Copy it immediately; the generator does not send it to Stop-GIS.
6. Push the generated app to GitHub. Do not commit `.env` or `.streamlit/secrets.toml`.
7. In Streamlit Community Cloud, create the app and choose `app.py` for a new standalone repository
   or `preview_app/app.py` when publishing into the Stop-GIS repository.
8. Open **Advanced settings -> Secrets** and paste values based on
   `.streamlit/secrets.toml.example`:

   ```toml
   STOP_GIS_VOTE_DATABASE_URL = "postgresql://RUNTIME_USER:PASSWORD@HOST:5432/DATABASE?sslmode=require"
   STOP_GIS_VOTE_FINGERPRINT_SECRET = "YOUR_BROWSER_GENERATED_SECRET"
   ```

9. Deploy, open a stop with voting enabled, and confirm the app reports
   **Voting storage: PostgreSQL**. Submit a test vote and verify it remains after an app restart.

Do not use the owner-role URL as the deployed app secret. For serverless or highly concurrent
hosting, use your provider's supported pooled connection endpoint when available; the app also uses
a small bounded client-side connection pool.

## Other hosts

The same server-side environment variables work on Render, Railway, Fly.io, Docker, Kubernetes,
virtual machines, and local servers:

1. Install `requirements.txt`.
2. Run the migration once with the owner role.
3. Run `scripts/verify_database.py` with the runtime role.
4. Add both required values to the host's encrypted environment/secret manager.
5. Start with `streamlit run app.py` (or the repository's generated app path).

For local PostgreSQL or a private network endpoint, the migration and verification helpers block
the target by default as SSRF protection. A trusted self-hosted operator can explicitly set
`STOP_GIS_ALLOW_PRIVATE_DATABASE_HOSTS=true` for that process. Never enable that override on a
publicly accessible Stop-GIS builder.

Client-address forwarding is disabled by default. If every request reaches the app through a trusted
reverse proxy that removes client-supplied forwarding headers and writes its own, set
`STOP_GIS_TRUST_PROXY_HEADERS=true`. Do not enable it when clients can reach the app directly or the
proxy passes through arbitrary `Forwarded`, `X-Forwarded-For`, or `CF-Connecting-IP` values.

## Local SQLite

When no PostgreSQL URL is configured, the generated app uses `.shade_gis_votes.sqlite3`. This is
useful for local evaluation only. Ephemeral hosts may delete the file, and multiple replicas do not
share it. If a PostgreSQL URL is configured but fails, the app fails closed and does not silently
fall back to SQLite.

## Migrations, backups, and recovery

Back up the user-owned database using your provider's backup and restore controls before a schema
change. Generated migrations are additive and versioned; they do not delete vote data. Concurrent
migration attempts serialize on an advisory lock. An older generated app refuses to run against a
newer schema instead of attempting a destructive downgrade.

If deployment fails, check TLS settings, provider network rules, PostgreSQL 14+ compatibility,
runtime grants, and whether the schema was initialized. Errors are intentionally sanitized so the
connection string is not echoed to the UI or scripts.
