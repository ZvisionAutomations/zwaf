-- Story-044 — rollback da memória de lead. Remove apenas as colunas adicionadas
-- pela 006_lead_memory.sql. Idempotente.

BEGIN;

ALTER TABLE leads
  DROP COLUMN IF EXISTS primary_symptom_enc,
  DROP COLUMN IF EXISTS memory_summary_enc,
  DROP COLUMN IF EXISTS objections,
  DROP COLUMN IF EXISTS next_best_action,
  DROP COLUMN IF EXISTS memory_updated_at,
  DROP COLUMN IF EXISTS memory_purged_at;

COMMIT;
