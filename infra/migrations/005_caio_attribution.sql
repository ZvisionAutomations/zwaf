BEGIN;

CREATE TABLE IF NOT EXISTS lead_attribution (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    lead_phone TEXT NOT NULL,
    ctwa_clid TEXT,
    source_id TEXT,
    source_type TEXT,
    source_url TEXT,
    headline TEXT,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_lead_attr_tenant_phone
    ON lead_attribution(tenant_id, lead_phone);

CREATE INDEX IF NOT EXISTS idx_lead_attr_tenant_ad
    ON lead_attribution(tenant_id, source_id);

CREATE INDEX IF NOT EXISTS idx_lead_attr_ctwa
    ON lead_attribution(ctwa_clid);

CREATE TABLE IF NOT EXISTS meta_ad_hierarchy (
    tenant_id TEXT NOT NULL,
    ad_id TEXT NOT NULL,
    adset_id TEXT,
    campaign_id TEXT,
    ad_name TEXT,
    adset_name TEXT,
    campaign_name TEXT,
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, ad_id)
);

CREATE OR REPLACE VIEW caio_attribution_signal AS
SELECT
    la.tenant_id,
    la.ctwa_clid,
    la.source_id AS ad_id,
    h.adset_id,
    h.campaign_id,
    h.campaign_name,
    h.adset_name,
    h.ad_name,
    o.id AS order_id,
    o.product_id,
    o.status AS order_status,
    o.total_cents AS revenue_cents,
    o.paid_at,
    (o.paid_at IS NOT NULL) AS is_paid,
    CASE
        WHEN la.ctwa_clid IS NOT NULL THEN 'high'
        WHEN la.source_id IS NOT NULL OR la.source_url IS NOT NULL THEN 'medium'
        ELSE 'low'
    END AS attribution_confidence,
    la.captured_at,
    o.created_at AS order_created_at
FROM lead_attribution la
LEFT JOIN orders o
       ON o.tenant_id = la.tenant_id
      AND o.lead_phone = la.lead_phone
LEFT JOIN meta_ad_hierarchy h
       ON h.tenant_id = la.tenant_id
      AND h.ad_id = la.source_id;

DO $$
BEGIN
   IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'caio_ro') THEN
      CREATE ROLE caio_ro LOGIN PASSWORD '__SET_VIA_SECRET__';
   END IF;
END$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM caio_ro;
GRANT USAGE ON SCHEMA public TO caio_ro;
GRANT SELECT ON caio_attribution_signal TO caio_ro;

COMMIT;
