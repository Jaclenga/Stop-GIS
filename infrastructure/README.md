# Infrastructure

- `compose.yaml` starts the local PostgreSQL service.
- `database/schema.sql` defines a fresh database.
- `database/migrations/` contains additive upgrades for existing databases.

From the repository root, validate or start the service with:

```powershell
docker compose -f infrastructure/compose.yaml config
docker compose -f infrastructure/compose.yaml up -d
```
