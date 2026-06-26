-- story-065: commercial follow-up state tracking
-- Tracks per-lead/stage follow-up state for the commercial follow-up engine.
BEGIN;

CREATE TABLE IF NOT EXISTS commercial_followups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    lead_phone TEXT NOT NULL,
    stage TEXT NOT NULL,
    temperature TEXT NOT NULL DEFAULT 'warm',
    contacts_sent INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending', 'sending', 'replied', 'converted',
            'limit_reached', 'opted_out', 'medical_risk', 'error'
        )),
    next_send_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, lead_phone, stage)
);

CREATE INDEX IF NOT EXISTS idx_commercial_followups_due
    ON commercial_followups (tenant_id, next_send_at)
    WHERE status = 'pending';

COMMIT;