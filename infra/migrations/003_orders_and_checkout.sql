-- Orders, encrypted checkout PII and delivery-based follow-up.

CREATE TABLE IF NOT EXISTS lead_profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    phone TEXT NOT NULL,
    full_name_encrypted TEXT,
    document_encrypted TEXT,
    document_hash TEXT,
    document_last4 TEXT,
    document_type TEXT,
    opt_out_at TIMESTAMPTZ,
    opt_out_reason TEXT,
    contact_status TEXT NOT NULL DEFAULT 'active',
    consent_source TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_lead_profiles_tenant_phone
    ON lead_profiles(tenant_id, phone);

CREATE INDEX IF NOT EXISTS idx_lead_profiles_contact_status
    ON lead_profiles(tenant_id, contact_status);

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS opt_out_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS opt_out_reason TEXT,
    ADD COLUMN IF NOT EXISTS contact_status TEXT DEFAULT 'active';

CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    lead_phone TEXT NOT NULL,
    product_id TEXT NOT NULL,
    sku TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    subtotal_cents INTEGER NOT NULL DEFAULT 0,
    shipping_cents INTEGER NOT NULL DEFAULT 0,
    discount_cents INTEGER NOT NULL DEFAULT 0,
    total_cents INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    billing_type TEXT NOT NULL DEFAULT 'PIX',
    asaas_customer_id TEXT,
    asaas_payment_id TEXT,
    asaas_payment_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    paid_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_orders_tenant_status
    ON orders(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_orders_tenant_phone
    ON orders(tenant_id, lead_phone);

CREATE TABLE IF NOT EXISTS order_delivery_addresses (
    order_id UUID PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    recipient_name_encrypted TEXT,
    postal_code_encrypted TEXT,
    street_encrypted TEXT,
    number_encrypted TEXT,
    complement_encrypted TEXT,
    district_encrypted TEXT,
    city_encrypted TEXT,
    state_encrypted TEXT,
    address_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS shipments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    provider TEXT DEFAULT 'manual',
    external_shipment_id TEXT,
    tracking_code TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    delivered_by TEXT,
    posted_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shipments_order_status
    ON shipments(order_id, status);

CREATE TABLE IF NOT EXISTS delivery_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    shipment_id UUID NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL DEFAULT 'manual',
    raw_payload_redacted JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS followup_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    scheduled_for TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'scheduled',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(order_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_followup_events_due
    ON followup_events(status, scheduled_for);

CREATE TABLE IF NOT EXISTS webhook_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    instance TEXT,
    event_id TEXT,
    event_type TEXT NOT NULL,
    payload_hash TEXT,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(provider, tenant_id, event_id)
);
