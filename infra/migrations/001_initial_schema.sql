-- ZWAF Initial Schema
-- Suporta multi-tenancy: tenant_id em todas as tabelas relevantes

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ─── Leads ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    phone TEXT NOT NULL,
    name TEXT,
    email TEXT,
    purchase_history JSONB DEFAULT '[]',
    last_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_leads_tenant_phone ON leads(tenant_id, phone);

-- ─── Sessões de Conversa ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS zwaf_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    messages JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_tenant_session ON zwaf_sessions(tenant_id, session_id);

-- ─── Knowledge Base (pgvector — Fase 2) ──────────────────────

CREATE TABLE IF NOT EXISTS knowledge_base (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    source_file TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(1536),  -- OpenAI text-embedding-3-small
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_tenant ON knowledge_base(tenant_id);
-- Índice vetorial (IVFFlat) criado separadamente quando dados estiverem populados

-- ─── Payments (registro de webhooks Abacate Pay) ─────────────

CREATE TABLE IF NOT EXISTS payment_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    payment_id TEXT NOT NULL,
    lead_phone TEXT NOT NULL,
    product_id TEXT,
    amount_cents INTEGER,
    status TEXT NOT NULL,  -- PAID, PENDING, EXPIRED, REFUNDED
    raw_payload JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payments_tenant_status ON payment_events(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_payments_payment_id ON payment_events(payment_id);

-- Conversion events (sentimento, intencao, link de pagamento)

CREATE TABLE IF NOT EXISTS conversion_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    lead_phone TEXT NOT NULL,
    session_id TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    buying_intent TEXT NOT NULL,
    action TEXT NOT NULL,
    should_send_payment_link BOOLEAN DEFAULT FALSE,
    confidence NUMERIC(4, 3) DEFAULT 0,
    reasons JSONB DEFAULT '[]',
    raw_signal JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversion_events_tenant_created ON conversion_events(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversion_events_tenant_action ON conversion_events(tenant_id, action);
