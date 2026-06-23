-- story-065: commercial follow-up engine state
-- Persists per-lead/per-stage follow-up counters and due times.
BEGIN;

CREATE TABLE IF NOT EXISTS commercial_followups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    phone TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'sending', 'completed', 'blocked')),
    contacts_sent INTEGER NOT NULL DEFAULT 0 CHECK (contacts_sent >= 0),
    max_contacts INTEGER NOT NULL DEFAULT 0 CHECK (max_contacts >= 0),
    next_send_at TIMESTAMPTZ,
    last_sent_at TIMESTAMPTZ,
    last_replied_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ,
    last_template_id TEXT,
    last_temperature TEXT,
    context_messages TEXT NOT NULL DEFAULT '',
    block_reason TEXT,
    locked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, phone, stage)
);

CREATE INDEX IF NOT EXISTS idx_commercial_followups_due
    ON commercial_followups (tenant_id, next_send_at)
    WHERE status = 'scheduled';

CREATE INDEX IF NOT EXISTS idx_commercial_followups_phone
    ON commercial_followups (tenant_id, phone);

COMMIT;
