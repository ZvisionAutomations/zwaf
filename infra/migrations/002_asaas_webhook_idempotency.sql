-- Asaas webhook idempotency.
-- Keeps repeated webhook deliveries from duplicating payment_events or purchase_history.

ALTER TABLE payment_events
    ADD COLUMN IF NOT EXISTS provider TEXT DEFAULT 'asaas',
    ADD COLUMN IF NOT EXISTS provider_event_id TEXT;

UPDATE payment_events
SET provider = 'asaas'
WHERE provider IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_events_tenant_provider_event
    ON payment_events(tenant_id, provider, provider_event_id)
    WHERE provider_event_id IS NOT NULL AND provider_event_id <> '';
