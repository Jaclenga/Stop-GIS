-- Relational platform schema for Stop-GIS.
-- The Streamlit builder uses a SQLite implementation of this shape by default;
-- this file is the Postgres-ready schema for shared deployments.

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  agency TEXT,
  region TEXT,
  description TEXT,
  owners TEXT,
  visibility TEXT NOT NULL DEFAULT 'Private' CHECK (visibility IN ('Public', 'Private')),
  dataset_version TEXT,
  methodology_version TEXT,
  source_name TEXT,
  source_license TEXT,
  source_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_settings (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  methodology_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  visualization_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  deployment_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  scoring_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS assessment_modes (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  mode_key TEXT NOT NULL,
  label TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  operational_definition TEXT NOT NULL DEFAULT '',
  value_type TEXT NOT NULL CHECK (value_type IN ('categorical', 'boolean', 'number', 'text')),
  allowed_values_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  ordering_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  multiple BOOLEAN NOT NULL DEFAULT FALSE,
  allow_comment BOOLEAN NOT NULL DEFAULT TRUE,
  collect_confidence BOOLEAN NOT NULL DEFAULT TRUE,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  required BOOLEAN NOT NULL DEFAULT FALSE,
  sort_order INTEGER NOT NULL DEFAULT 1,
  measurement_level TEXT NOT NULL DEFAULT 'nominal'
    CHECK (measurement_level IN ('nominal', 'ordinal', 'interval', 'ratio')),
  scoring_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  display_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (project_id, mode_key)
);

CREATE TABLE IF NOT EXISTS shade_taxonomy (
  id BIGSERIAL PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT,
  color TEXT,
  sort_order INTEGER NOT NULL DEFAULT 1,
  UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS stops (
  id BIGSERIAL PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stop_id TEXT NOT NULL,
  stop_name TEXT NOT NULL,
  stop_lat DOUBLE PRECISION,
  stop_lon DOUBLE PRECISION,
  agency TEXT,
  routes TEXT,
  municipality TEXT,
  shading TEXT,
  shade_coverage TEXT,
  shade_sources TEXT,
  review_status TEXT,
  confidence DOUBLE PRECISION,
  ridership DOUBLE PRECISION,
  priority_score DOUBLE PRECISION,
  extra_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, stop_id)
);

-- Generated public apps can use this table in a shared PostgreSQL database.
-- It intentionally does not require a projects row because the voting store may be deployed alone.
CREATE TABLE IF NOT EXISTS shade_votes (
  id BIGSERIAL PRIMARY KEY,
  study_id TEXT NOT NULL,
  stop_id TEXT NOT NULL,
  voter_id TEXT NOT NULL,
  coverage_status TEXT NOT NULL,
  shade_sources TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (study_id, stop_id, voter_id)
);

-- Holds the random HMAC key used to pseudonymize voter network/browser signals when an
-- external STOP_GIS_VOTE_FINGERPRINT_SECRET is not configured. Raw signals are never stored.
CREATE TABLE IF NOT EXISTS shade_vote_settings (
  setting_key TEXT PRIMARY KEY,
  setting_value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS shade_votes_rate_limit_idx
  ON shade_votes (study_id, voter_id, created_at);

CREATE TABLE IF NOT EXISTS images (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stop_id TEXT,
  uri TEXT NOT NULL,
  storage_path TEXT,
  image_type TEXT,
  source TEXT,
  captured_at TIMESTAMPTZ,
  attribution TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT tenant_images_project_id_key UNIQUE (project_id, id),
  CONSTRAINT tenant_images_stop_fk FOREIGN KEY (project_id, stop_id)
    REFERENCES stops(project_id, stop_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shade_labels (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stop_id TEXT NOT NULL,
  image_id TEXT,
  labeler_id TEXT,
  labeler_role TEXT,
  shade_category TEXT,
  shade_coverage TEXT,
  shade_sources TEXT,
  confidence DOUBLE PRECISION,
  notes TEXT,
  source TEXT NOT NULL DEFAULT 'manual',
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT tenant_labels_stop_fk FOREIGN KEY (project_id, stop_id)
    REFERENCES stops(project_id, stop_id) ON DELETE CASCADE,
  CONSTRAINT tenant_labels_image_fk FOREIGN KEY (project_id, image_id)
    REFERENCES images(project_id, id)
);

-- Generic immutable assessments. shade_labels remains as a compatibility
-- table for existing shade studies and community-voting deployments.
CREATE TABLE IF NOT EXISTS assessments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stop_id TEXT NOT NULL,
  reviewer_id TEXT,
  reviewer_role TEXT,
  evidence_method TEXT NOT NULL DEFAULT 'manual',
  assessment_values_json JSONB NOT NULL,
  comments_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  confidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  submission_type TEXT NOT NULL DEFAULT 'independent'
    CHECK (submission_type IN ('independent', 'adjudication')),
  supersedes_id TEXT REFERENCES assessments(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT tenant_assessments_stop_fk FOREIGN KEY (project_id, stop_id)
    REFERENCES stops(project_id, stop_id) ON DELETE CASCADE
);

CREATE OR REPLACE FUNCTION prevent_assessment_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'assessment submissions are immutable';
END;
$$;

DROP TRIGGER IF EXISTS immutable_assessments_update ON assessments;
CREATE TRIGGER immutable_assessments_update
BEFORE UPDATE ON assessments
FOR EACH ROW EXECUTE FUNCTION prevent_assessment_update();

CREATE OR REPLACE FUNCTION validate_assessment_supersession()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.supersedes_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM assessments AS prior
    WHERE prior.id = NEW.supersedes_id
      AND prior.project_id = NEW.project_id
      AND prior.stop_id = NEW.stop_id
  ) THEN
    RAISE EXCEPTION 'superseded assessment must belong to the same project and stop';
  END IF;
  IF NEW.supersedes_id IS NOT NULL AND NEW.submission_type <> 'adjudication' THEN
    RAISE EXCEPTION 'only an adjudication may supersede an assessment';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS valid_assessment_supersession ON assessments;
CREATE TRIGGER valid_assessment_supersession
BEFORE INSERT ON assessments
FOR EACH ROW EXECUTE FUNCTION validate_assessment_supersession();

CREATE OR REPLACE FUNCTION enforce_shade_label_image_stop()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.image_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM images
    WHERE id = NEW.image_id
      AND project_id = NEW.project_id
      AND (stop_id IS NULL OR stop_id = NEW.stop_id)
  ) THEN
    RAISE EXCEPTION 'label image must belong to the same project and stop'
      USING ERRCODE = 'foreign_key_violation';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tenant_labels_image_stop_guard ON shade_labels;
CREATE TRIGGER tenant_labels_image_stop_guard
BEFORE INSERT OR UPDATE OF project_id, stop_id, image_id ON shade_labels
FOR EACH ROW EXECUTE FUNCTION enforce_shade_label_image_stop();

CREATE OR REPLACE FUNCTION enforce_image_label_stop_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM shade_labels AS label
    WHERE label.project_id = OLD.project_id
      AND label.image_id = OLD.id
      AND (
        NEW.id <> OLD.id
        OR label.project_id <> NEW.project_id
        OR (NEW.stop_id IS NOT NULL AND label.stop_id <> NEW.stop_id)
      )
  ) THEN
    RAISE EXCEPTION 'image update would invalidate linked labels'
      USING ERRCODE = 'foreign_key_violation';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tenant_image_label_stop_guard ON images;
CREATE TRIGGER tenant_image_label_stop_guard
BEFORE UPDATE OF id, project_id, stop_id ON images
FOR EACH ROW EXECUTE FUNCTION enforce_image_label_stop_update();

CREATE OR REPLACE FUNCTION detach_labels_before_image_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE shade_labels
  SET image_id = NULL
  WHERE project_id = OLD.project_id AND image_id = OLD.id;
  RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS tenant_image_label_delete_guard ON images;
CREATE TRIGGER tenant_image_label_delete_guard
BEFORE DELETE ON images
FOR EACH ROW EXECUTE FUNCTION detach_labels_before_image_delete();

-- Private, builder-side workflow for independent image coding. Public study apps do not use
-- these tables. Results remain hidden until the protocol advances out of the coding phase.
CREATE TABLE IF NOT EXISTS blind_protocols (
  project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  phase TEXT NOT NULL DEFAULT 'setup' CHECK (phase IN ('setup', 'coding', 'adjudication', 'closed')),
  codebook_version TEXT NOT NULL,
  target_ratings INTEGER NOT NULL DEFAULT 3 CHECK (target_ratings >= 3),
  assessment_unit TEXT NOT NULL DEFAULT 'image' CHECK (assessment_unit IN ('image', 'stop')),
  agreement_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.67 CHECK (agreement_threshold BETWEEN 0 AND 1),
  instructions TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS blind_images (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  image_id TEXT NOT NULL,
  display_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT tenant_blind_images_project_id_key UNIQUE (project_id, id),
  UNIQUE (project_id, image_id),
  UNIQUE (project_id, display_id),
  CONSTRAINT tenant_blind_images_image_fk FOREIGN KEY (project_id, image_id)
    REFERENCES images(project_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blind_stops (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stop_id TEXT NOT NULL,
  display_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT tenant_blind_stops_project_id_key UNIQUE (project_id, id),
  UNIQUE (project_id, stop_id),
  UNIQUE (project_id, display_id),
  CONSTRAINT tenant_blind_stops_stop_fk FOREIGN KEY (project_id, stop_id)
    REFERENCES stops(project_id, stop_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blind_assignments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  blind_image_id TEXT NOT NULL,
  coder_id TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned', 'submitted')),
  assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  submitted_at TIMESTAMPTZ,
  CONSTRAINT tenant_blind_assignments_project_id_key UNIQUE (project_id, id),
  UNIQUE (project_id, blind_image_id, coder_id),
  CONSTRAINT tenant_blind_assignments_image_fk FOREIGN KEY (project_id, blind_image_id)
    REFERENCES blind_images(project_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blind_ratings (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  assignment_id TEXT NOT NULL,
  codebook_version TEXT NOT NULL,
  shade_source TEXT NOT NULL,
  coverage TEXT NOT NULL,
  waiting_area_covered TEXT NOT NULL,
  permanence TEXT NOT NULL,
  image_adequacy TEXT NOT NULL,
  confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
  location_recognized TEXT NOT NULL,
  review_method TEXT NOT NULL DEFAULT 'standardized_image',
  submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, assignment_id),
  CONSTRAINT tenant_blind_ratings_assignment_fk FOREIGN KEY (project_id, assignment_id)
    REFERENCES blind_assignments(project_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blind_stop_assignments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  blind_stop_id TEXT NOT NULL,
  coder_id TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned', 'submitted')),
  assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  submitted_at TIMESTAMPTZ,
  CONSTRAINT tenant_blind_stop_assignments_project_id_key UNIQUE (project_id, id),
  UNIQUE (project_id, blind_stop_id, coder_id),
  CONSTRAINT tenant_blind_stop_assignments_stop_fk FOREIGN KEY (project_id, blind_stop_id)
    REFERENCES blind_stops(project_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blind_stop_ratings (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  assignment_id TEXT NOT NULL,
  codebook_version TEXT NOT NULL,
  shade_source TEXT NOT NULL,
  coverage TEXT NOT NULL,
  waiting_area_covered TEXT NOT NULL,
  permanence TEXT NOT NULL,
  image_adequacy TEXT NOT NULL,
  confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
  location_recognized TEXT NOT NULL,
  review_method TEXT NOT NULL,
  submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, assignment_id),
  CONSTRAINT tenant_blind_stop_ratings_assignment_fk FOREIGN KEY (project_id, assignment_id)
    REFERENCES blind_stop_assignments(project_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blind_adjudications (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  blind_image_id TEXT NOT NULL,
  adjudicator_id TEXT NOT NULL,
  codebook_version TEXT NOT NULL,
  shade_source TEXT NOT NULL,
  coverage TEXT NOT NULL,
  waiting_area_covered TEXT NOT NULL,
  permanence TEXT NOT NULL,
  image_adequacy TEXT NOT NULL,
  confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
  notes TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, blind_image_id),
  CONSTRAINT tenant_blind_adjudications_image_fk FOREIGN KEY (project_id, blind_image_id)
    REFERENCES blind_images(project_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blind_stop_adjudications (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  blind_stop_id TEXT NOT NULL,
  adjudicator_id TEXT NOT NULL,
  codebook_version TEXT NOT NULL,
  shade_source TEXT NOT NULL,
  coverage TEXT NOT NULL,
  waiting_area_covered TEXT NOT NULL,
  permanence TEXT NOT NULL,
  image_adequacy TEXT NOT NULL,
  confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
  notes TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, blind_stop_id),
  CONSTRAINT tenant_blind_stop_adjudications_stop_fk FOREIGN KEY (project_id, blind_stop_id)
    REFERENCES blind_stops(project_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_history (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  stop_id TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  notes TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT tenant_review_history_stop_fk FOREIGN KEY (project_id, stop_id)
    REFERENCES stops(project_id, stop_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS releases (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  version TEXT NOT NULL,
  dataset_version TEXT,
  methodology_version TEXT,
  taxonomy_version TEXT,
  import_version TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
  released_at TIMESTAMPTZ,
  artifact_manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, version)
);

CREATE TABLE IF NOT EXISTS import_logs (
  id BIGSERIAL PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source TEXT,
  format TEXT,
  rows INTEGER,
  imported_at TIMESTAMPTZ,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_stops_project ON stops(project_id);
CREATE INDEX IF NOT EXISTS idx_images_project_stop ON images(project_id, stop_id);
CREATE INDEX IF NOT EXISTS idx_labels_project_stop ON shade_labels(project_id, stop_id);
CREATE INDEX IF NOT EXISTS idx_assessments_project_stop ON assessments(project_id, stop_id, created_at);
CREATE INDEX IF NOT EXISTS idx_blind_images_project ON blind_images(project_id);
CREATE INDEX IF NOT EXISTS idx_blind_stops_project ON blind_stops(project_id);
CREATE INDEX IF NOT EXISTS idx_blind_assignments_coder ON blind_assignments(project_id, coder_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_blind_stop_assignments_coder ON blind_stop_assignments(project_id, coder_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_blind_ratings_project ON blind_ratings(project_id);
CREATE INDEX IF NOT EXISTS idx_blind_stop_ratings_project ON blind_stop_ratings(project_id);
CREATE INDEX IF NOT EXISTS idx_blind_adjudications_project ON blind_adjudications(project_id);
CREATE INDEX IF NOT EXISTS idx_blind_stop_adjudications_project ON blind_stop_adjudications(project_id);
CREATE INDEX IF NOT EXISTS idx_review_project_stop ON review_history(project_id, stop_id);
CREATE INDEX IF NOT EXISTS idx_releases_project ON releases(project_id);
