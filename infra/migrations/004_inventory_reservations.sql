-- Inventory reservations to prevent oversell (story-034).
-- Atomic stock reservation BEFORE generating an Asaas payment link, with a
-- TTL-based expiry, idempotent webhook confirmation/release, and an
-- append-only movements ledger for audit.
--
-- Rollback (manual):
--   DROP TABLE IF EXISTS inventory_movements;
--   DROP TABLE IF EXISTS inventory_reservations;
--   DROP TABLE IF EXISTS inventory_items;

-- ---------------------------------------------------------------------------
-- inventory_items: one row per (tenant, product). The single source of truth
-- for how many units can still be reserved.
--   available = on_hand_qty - reserved_qty - committed_qty - safety_buffer_qty
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    sku TEXT,
    on_hand_qty INTEGER NOT NULL DEFAULT 0,
    reserved_qty INTEGER NOT NULL DEFAULT 0,
    committed_qty INTEGER NOT NULL DEFAULT 0,
    safety_buffer_qty INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, product_id),
    CONSTRAINT inventory_items_on_hand_nonneg CHECK (on_hand_qty >= 0),
    CONSTRAINT inventory_items_reserved_nonneg CHECK (reserved_qty >= 0),
    CONSTRAINT inventory_items_committed_nonneg CHECK (committed_qty >= 0),
    CONSTRAINT inventory_items_buffer_nonneg CHECK (safety_buffer_qty >= 0),
    -- Hard no-oversell invariant: never commit/reserve more than physically held.
    CONSTRAINT inventory_items_no_oversell
        CHECK (reserved_qty + committed_qty <= on_hand_qty)
);

CREATE INDEX IF NOT EXISTS idx_inventory_items_tenant_product
    ON inventory_items(tenant_id, product_id);

-- ---------------------------------------------------------------------------
-- inventory_reservations: one reservation per order. UNIQUE(order_id) makes the
-- reserve step idempotent; UNIQUE(tenant_id, idempotency_key) dedupes retries.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory_reservations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    status TEXT NOT NULL DEFAULT 'active',
    reserved_until TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (order_id),
    UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT inventory_reservations_status_valid
        CHECK (status IN ('active', 'confirmed', 'released', 'expired', 'failed'))
);

-- Drives the release-expired sweep: find active reservations past their TTL.
CREATE INDEX IF NOT EXISTS idx_inventory_reservations_active_expiry
    ON inventory_reservations(tenant_id, status, reserved_until);

-- ---------------------------------------------------------------------------
-- inventory_movements: append-only audit ledger. Never updated or deleted.
-- quantity_delta sign convention:
--   reserved        +qty  (units moved into reserved)
--   confirmed_sale  +qty  (units moved reserved -> committed)
--   released        -qty  (reserved units freed)
--   expired         -qty  (reserved units freed by TTL sweep)
--   manual_adjustment  ±delta (operator correction, reason required)
--   refund_review    0    (informational; stock not returned automatically)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory_movements (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    order_id UUID,
    reservation_id UUID,
    movement_type TEXT NOT NULL,
    quantity_delta INTEGER NOT NULL,
    reason TEXT,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT inventory_movements_type_valid CHECK (
        movement_type IN (
            'reserved', 'confirmed_sale', 'released',
            'expired', 'manual_adjustment', 'refund_review'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_inventory_movements_tenant_product
    ON inventory_movements(tenant_id, product_id, created_at);

CREATE INDEX IF NOT EXISTS idx_inventory_movements_order
    ON inventory_movements(order_id);

-- ---------------------------------------------------------------------------
-- Seed (idempotent). Starting point derived from each tenant config
-- `inventory.initial_stock`. THESE NUMBERS MUST BE CONFIRMED BY PHYSICAL COUNT
-- before connecting WhatsApp — adjust afterwards via the manual_adjustment path
-- (zwaf inventory adjust ...). ON CONFLICT keeps re-runs safe and never
-- overwrites a count already corrected in production.
-- ---------------------------------------------------------------------------
INSERT INTO inventory_items (tenant_id, product_id, sku, on_hand_qty)
VALUES
    ('livia-raiz-vital', 'new-woman', 'nw-001', 510),
    ('caio-alpha-pulse', 'alpha-pulse', 'ap-001', 275)
ON CONFLICT (tenant_id, product_id) DO NOTHING;
