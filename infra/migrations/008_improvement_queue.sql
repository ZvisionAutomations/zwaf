-- story-052: persistência da ImprovementQueue
BEGIN;

CREATE TABLE IF NOT EXISTS improvement_candidates (
    id            TEXT        PRIMARY KEY,
    tenant_id     TEXT        NOT NULL DEFAULT '',
    kind          TEXT        NOT NULL,
    summary       TEXT        NOT NULL,
    evidence      JSONB       NOT NULL DEFAULT '{}',
    status        TEXT        NOT NULL DEFAULT 'suggested',
    reviewed_by   TEXT,
    review_note   TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_imprcand_tenant_status
    ON improvement_candidates (tenant_id, status);

CREATE TABLE IF NOT EXISTS improvement_review_log (
    id            BIGSERIAL   PRIMARY KEY,
    candidate_id  TEXT        NOT NULL REFERENCES improvement_candidates(id),
    from_status   TEXT        NOT NULL,
    to_status     TEXT        NOT NULL,
    actor         TEXT        NOT NULL,
    note          TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
