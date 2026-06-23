BEGIN;

REVOKE SELECT ON caio_attribution_signal FROM caio_ro;
DROP VIEW IF EXISTS caio_attribution_signal;
DROP TABLE IF EXISTS meta_ad_hierarchy;
DROP TABLE IF EXISTS lead_attribution;

DO $$
BEGIN
   IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'caio_ro') THEN
      DROP ROLE caio_ro;
   END IF;
END$$;

COMMIT;
