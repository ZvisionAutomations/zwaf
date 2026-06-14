BEGIN;

CREATE TABLE IF NOT EXISTS ab_assignments (
    phone TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    test_name TEXT NOT NULL,
    variant TEXT NOT NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (phone, tenant_id, test_name)
);

CREATE INDEX IF NOT EXISTS idx_ab_tenant_test ON ab_assignments (tenant_id, test_name);

COMMIT;
