BEGIN;

-- Serializes concurrent migration commands for this application.
SELECT pg_advisory_xact_lock(6426939476836841183);

CREATE TABLE IF NOT EXISTS shade_votes (
    id BIGSERIAL PRIMARY KEY,
    study_id TEXT NOT NULL,
    stop_id TEXT NOT NULL,
    voter_id TEXT NOT NULL,
    network_id TEXT NOT NULL DEFAULT '',
    coverage_status TEXT NOT NULL,
    shade_sources TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (study_id, stop_id, voter_id)
);

ALTER TABLE shade_votes
    ADD COLUMN IF NOT EXISTS network_id TEXT NOT NULL DEFAULT '';
ALTER TABLE shade_votes
    ADD COLUMN IF NOT EXISTS shade_sources TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS shade_vote_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL
);

DO $$
DECLARE
    installed_version INTEGER;
BEGIN
    SELECT setting_value::INTEGER INTO installed_version
      FROM shade_vote_settings
     WHERE setting_key = 'schema_version';
    IF installed_version > 1 THEN
        RAISE EXCEPTION 'Voting schema version % is newer than this migration supports', installed_version;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS shade_votes_rate_limit_idx
    ON shade_votes (study_id, voter_id, created_at);
CREATE INDEX IF NOT EXISTS shade_votes_network_rate_limit_idx
    ON shade_votes (study_id, network_id, created_at);
CREATE INDEX IF NOT EXISTS shade_votes_stop_rate_limit_idx
    ON shade_votes (study_id, stop_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS shade_votes_network_unique_idx
    ON shade_votes (study_id, stop_id, network_id)
    WHERE network_id <> '';

INSERT INTO shade_vote_settings (setting_key, setting_value)
VALUES ('schema_version', '1')
ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value;

COMMIT;
