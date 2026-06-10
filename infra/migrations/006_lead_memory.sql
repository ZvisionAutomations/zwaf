-- Story-044 — Memória de Lead da Lívia (vendedor com memória)
-- Adiciona a camada de memória semântica ao lead, 1:1 com a linha de `leads`.
-- Campos de saúde (Art. 11 LGPD) são cifrados na aplicação (Fernet) antes do INSERT;
-- objections/next_best_action são comerciais (não-clínicos) e ficam em claro.
-- Idempotente: seguro reaplicar (padrão das migrations 001-005).

BEGIN;

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS primary_symptom_enc TEXT,        -- cifrado (Fernet) — dado de saúde
  ADD COLUMN IF NOT EXISTS memory_summary_enc  TEXT,        -- cifrado — anotação CRM (2-3 linhas)
  ADD COLUMN IF NOT EXISTS objections          JSONB DEFAULT '[]'::jsonb,  -- comercial, claro
  ADD COLUMN IF NOT EXISTS next_best_action    TEXT,        -- comercial, claro, curto
  ADD COLUMN IF NOT EXISTS memory_updated_at   TIMESTAMPTZ, -- janela de retenção / freshness
  ADD COLUMN IF NOT EXISTS memory_purged_at    TIMESTAMPTZ; -- carimbo de purga (opt-out / retenção)

COMMIT;
