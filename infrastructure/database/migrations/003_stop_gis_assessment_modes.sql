-- Additive Shade-GIS -> Stop-GIS migration. Existing shade tables and values
-- remain available as compatibility projections.

ALTER TABLE project_settings
  ADD COLUMN IF NOT EXISTS scoring_json JSONB NOT NULL DEFAULT '[]'::jsonb;

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
  measurement_level TEXT NOT NULL DEFAULT 'nominal',
  scoring_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  display_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (project_id, mode_key)
);

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
  FOREIGN KEY (project_id, stop_id)
    REFERENCES stops(project_id, stop_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_assessments_project_stop
  ON assessments(project_id, stop_id, created_at);

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

-- Projects without generic definitions receive the two coordinated shade modes.
INSERT INTO assessment_modes (
  project_id, mode_key, label, description, operational_definition, value_type,
  allowed_values_json, ordering_json, multiple, enabled, sort_order,
  measurement_level, scoring_json, display_json
)
SELECT p.id, seed.mode_key, seed.label, seed.description, seed.definition,
       'categorical', seed.allowed_values, seed.ordering, seed.multiple,
       TRUE, seed.sort_order, seed.measurement_level, seed.scoring,
       '{"map":true,"filter":true,"summary":true,"export":true}'::jsonb
FROM projects AS p
CROSS JOIN (
  VALUES
    ('shade_coverage', 'Shade coverage', 'Visible shade reaching the passenger waiting area.',
     'Classify shade at the place passengers reasonably wait.',
     '["none","limited","significant","unclear"]'::jsonb,
     '["none","limited","significant","unclear"]'::jsonb, FALSE, 1, 'ordinal',
     '{"none":0,"limited":0.5,"significant":1}'::jsonb),
    ('shade_source', 'Shade source', 'Features visibly providing shade.',
     'Select natural, purpose-built, incidental, or unclear sources.',
     '["natural","purpose_built","incidental","unclear"]'::jsonb,
     '[]'::jsonb, TRUE, 2, 'nominal', '{}'::jsonb)
) AS seed(mode_key, label, description, definition, allowed_values, ordering, multiple, sort_order, measurement_level, scoring)
WHERE NOT EXISTS (
  SELECT 1 FROM assessment_modes AS existing WHERE existing.project_id = p.id
)
ON CONFLICT (project_id, mode_key) DO NOTHING;
