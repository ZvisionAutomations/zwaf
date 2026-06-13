-- story-051: PIX re-engagement scheduler
-- Adds pix_due_date and reengagement_sent_at to orders for follow-up logic.
BEGIN;

ALTER TABLE orders ADD COLUMN IF NOT EXISTS pix_due_date DATE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS reengagement_sent_at TIMESTAMPTZ;

-- Partial index: only unpaid PIX orders with a due date and no re-engagement yet
CREATE INDEX IF NOT EXISTS idx_orders_pix_reengagement
    ON orders (tenant_id, pix_due_date)
    WHERE status = 'payment_link_created'
      AND billing_type = 'PIX'
      AND reengagement_sent_at IS NULL;

COMMIT;
