# STORY-048 Spec - Livia card hosted checkout

## Architecture Decision

Use Asaas Checkout (`POST /v3/checkouts`) for credit-card orders and keep the
current `/payments` flow for Pix.

Official Asaas documentation checked on 2026-06-13:

- `POST /v3/checkouts` creates a checkout session and returns `id` and `link`.
- Checkout request requires `billingTypes`, `chargeTypes`, `callback` and
  `items`; `externalReference` is supported.
- `customerData` is optional. If neither `customerData` nor `customer` is sent,
  the customer enters their own data on the hosted checkout page.
- Payment links (`POST /v3/paymentLinks`) are reusable; each fill creates a new
  charge and the webhook includes `paymentLink`.
- Payment webhooks send full charge data and payment status events; checkout
  webhooks also exist, but payment events are enough to confirm revenue and
  stock in the existing ZWAF flow.

Sources:

- https://docs.asaas.com/reference/create-new-checkout.md
- https://docs.asaas.com/docs/how-to-provide-customer-data.md
- https://docs.asaas.com/docs/checkout-link-and-customer-redirection.md
- https://docs.asaas.com/docs/payment-events.md

## Reuse-First Design

Reuse:

- `tools/payment.py` remains the payment tool factory and keeps Pix unchanged.
- `conversion/checkout_policy.py` remains the single checkout validation gate.
- `api/routes/payment_webhook.py` remains the Asaas payment webhook entry point.
- `memory/order_store.py` remains the order and encrypted PII persistence layer.
- `inventory_store.py` remains the stock reservation/confirmation layer.

Adapt:

- Make checkout validation billing-aware: Pix still requires full customer data
  in chat; `CREDIT_CARD` requires only tenant/product policy.
- Make the deterministic checkout flow skip chat collection when the chosen
  billing type is `CREDIT_CARD` and generate the hosted checkout immediately.
- In the payment tool, branch `CREDIT_CARD` to `/checkouts` without creating an
  Asaas customer and without sending `customerData`.
- Store the checkout id as `orders.asaas_payment_id` until Asaas later sends the
  concrete charge id; then remap the order by `externalReference`.
- Reconcile customer data from the payment webhook payload into `lead_profiles`
  and `order_delivery_addresses` using existing encrypted storage.

Create:

- No new production dependency.
- No migration for this cut; existing columns are sufficient.

## Reconciliation Model

1. When the customer chooses card, create an order draft with empty customer PII
   fields and reserve stock before calling Asaas.
2. Create an Asaas checkout with:
   - `billingTypes: ["CREDIT_CARD"]`
   - `chargeTypes: ["DETACHED"]`
   - `externalReference: tenant:phone:product_id:external_id`
   - one item for the selected product and quantity
   - no `customerData` and no `customer`
3. Store the returned checkout id/link on the order.
4. When a payment webhook arrives, parse `payment.externalReference`.
5. If no order is found by `asaas_payment_id = payment.id`, find the latest
   matching card order by tenant, lead phone, product id and external reference,
   then set `orders.asaas_payment_id = payment.id`.
6. Extract customer data from `payment.customerData`, `payment.customer` or
   top-level customer-like fields when present. Persist only via encrypted
   helpers; never log PII.
7. Confirm inventory only after the idempotency insert in `payment_events`
   succeeds. Duplicate webhook deliveries remain no-ops.
8. If the reservation expired or was released before payment confirmation,
   existing inventory logic marks the order for manual review.

## Constraints

- Pix behavior is not changed.
- No card/customer PII is collected in WhatsApp before the hosted checkout.
- No secrets or real PII in code/tests.
- Asaas callback URLs remain optional because previous production behavior
  showed account-domain validation can reject callback URLs.
