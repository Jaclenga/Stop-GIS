-- Preserve human-readable value labels and definitions without changing the
-- stable category codes stored in assessments and exports.

ALTER TABLE assessment_modes
  ADD COLUMN IF NOT EXISTS value_labels_json JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE assessment_modes
  ADD COLUMN IF NOT EXISTS value_definitions_json JSONB NOT NULL DEFAULT '{}'::jsonb;
