-- Upgrade existing Shade-GIS PostgreSQL databases with the tenant-boundary
-- constraints that are present in sql/schema.sql for fresh installations.

CREATE UNIQUE INDEX IF NOT EXISTS tenant_images_project_id_key
  ON images (project_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS tenant_blind_images_project_id_key
  ON blind_images (project_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS tenant_blind_stops_project_id_key
  ON blind_stops (project_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS tenant_blind_assignments_project_id_key
  ON blind_assignments (project_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS tenant_blind_stop_assignments_project_id_key
  ON blind_stop_assignments (project_id, id);

DO $$
DECLARE
  constraint_spec record;
BEGIN
  FOR constraint_spec IN
    SELECT * FROM (VALUES
      ('tenant_images_stop_fk', 'images',
       'FOREIGN KEY (project_id, stop_id) REFERENCES stops(project_id, stop_id) ON DELETE CASCADE'),
      ('tenant_labels_stop_fk', 'shade_labels',
       'FOREIGN KEY (project_id, stop_id) REFERENCES stops(project_id, stop_id) ON DELETE CASCADE'),
      ('tenant_labels_image_fk', 'shade_labels',
       'FOREIGN KEY (project_id, image_id) REFERENCES images(project_id, id)'),
      ('tenant_blind_images_image_fk', 'blind_images',
       'FOREIGN KEY (project_id, image_id) REFERENCES images(project_id, id) ON DELETE CASCADE'),
      ('tenant_blind_stops_stop_fk', 'blind_stops',
       'FOREIGN KEY (project_id, stop_id) REFERENCES stops(project_id, stop_id) ON DELETE CASCADE'),
      ('tenant_blind_assignments_image_fk', 'blind_assignments',
       'FOREIGN KEY (project_id, blind_image_id) REFERENCES blind_images(project_id, id) ON DELETE CASCADE'),
      ('tenant_blind_ratings_assignment_fk', 'blind_ratings',
       'FOREIGN KEY (project_id, assignment_id) REFERENCES blind_assignments(project_id, id) ON DELETE CASCADE'),
      ('tenant_blind_stop_assignments_stop_fk', 'blind_stop_assignments',
       'FOREIGN KEY (project_id, blind_stop_id) REFERENCES blind_stops(project_id, id) ON DELETE CASCADE'),
      ('tenant_blind_stop_ratings_assignment_fk', 'blind_stop_ratings',
       'FOREIGN KEY (project_id, assignment_id) REFERENCES blind_stop_assignments(project_id, id) ON DELETE CASCADE'),
      ('tenant_blind_adjudications_image_fk', 'blind_adjudications',
       'FOREIGN KEY (project_id, blind_image_id) REFERENCES blind_images(project_id, id) ON DELETE CASCADE'),
      ('tenant_blind_stop_adjudications_stop_fk', 'blind_stop_adjudications',
       'FOREIGN KEY (project_id, blind_stop_id) REFERENCES blind_stops(project_id, id) ON DELETE CASCADE'),
      ('tenant_review_history_stop_fk', 'review_history',
       'FOREIGN KEY (project_id, stop_id) REFERENCES stops(project_id, stop_id) ON DELETE CASCADE')
    ) AS specs(constraint_name, table_name, definition)
  LOOP
    IF NOT EXISTS (
      SELECT 1
      FROM pg_constraint
      WHERE conname = constraint_spec.constraint_name
        AND conrelid = constraint_spec.table_name::regclass
    ) THEN
      EXECUTE format(
        'ALTER TABLE %I ADD CONSTRAINT %I %s NOT VALID',
        constraint_spec.table_name,
        constraint_spec.constraint_name,
        constraint_spec.definition
      );
    END IF;
    EXECUTE format(
      'ALTER TABLE %I VALIDATE CONSTRAINT %I',
      constraint_spec.table_name,
      constraint_spec.constraint_name
    );
  END LOOP;
END;
$$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM shade_labels AS label
    JOIN images AS image
      ON image.project_id = label.project_id
     AND image.id = label.image_id
    WHERE label.image_id IS NOT NULL
      AND image.stop_id IS NOT NULL
      AND image.stop_id <> label.stop_id
  ) THEN
    RAISE EXCEPTION
      'Cannot install shade-label image/stop guard: historical mismatches must be corrected first'
      USING ERRCODE = 'foreign_key_violation';
  END IF;
END;
$$;

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
