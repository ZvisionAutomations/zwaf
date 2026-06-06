---
storyId: STORY-034
verdict: PASS
reviewer: Litmus (@quality-gate)
date: 2026-06-06
track: HEAVY (complexity 21) — concurrency / data-integrity focus
branch: caio/feat/story-034-inventory-reservations (merged PR #16)
commit: d2ebec4 (feat) / 75dfe35 (merge)
supersedes: CONCERNS (initial gate — C1 concurrency + C2 CodeRabbit pending)
checks:
  code_review: PASS
  unit_tests: PASS
  concurrency_postgres: PASS
  migrations_apply: PASS
  acceptance_criteria: PASS
  no_regressions: PASS
  security: PASS
  docs: PASS
evidence:
  unit_suite: "184 passed (tests/unit) — +48 over baseline; ruff + mypy clean"
  c1_concurrency: >-
    test_inventory_concurrency replayed 30x against a real pgvector/pgvector:pg16
    instance (ephemeral, isolated DB zwaf_ctest — production untouched). Every run:
    exactly 1 reservation won, the loser got 'unavailable', reserved_qty=1,
    available stayed 0 (never negative), 1 active reservation. 30/30 PASS.
  c1_method: >-
    Ephemeral Postgres + ephemeral python:3.12-slim on the VPS docker host via
    EC2 Instance Connect; real inventory_store.reserve_inventory (each call opens
    its own asyncpg connection → genuine row-lock race). Containers destroyed after.
  migrations_apply: >-
    001→002→003→004 applied in order with psql ON_ERROR_STOP=1 on a clean pgvector
    DB — all OK. First real application of migration 004 (oversell CHECK constraint,
    inventory_items / inventory_reservations / inventory_movements, idempotent seed).
issues:
  - severity: low
    category: process
    description: >-
      C2 (CodeRabbit automated review) was not executed — carried over process gap
      (station lacked gh/WSL with vault access; PR #16 merged via REST API). The code
      passed manual review, 184 unit tests, ruff, mypy, and the C1 concurrency proof,
      so this is a missing second-opinion pass, not an observed defect.
    recommendation: >-
      Run CodeRabbit on the merged range when a WSL+vault environment is available;
      non-blocking for go-live given C1 + unit coverage.
---

# QA Gate — Story 034: Inventory Reservations (oversell prevention)

**Verdict: PASS** — supersedes the initial **CONCERNS** verdict, which blocked on two
items: **C1** (concurrency proof on a real Postgres) and **C2** (CodeRabbit). C1 is now
**proven**; C2 remains a non-blocking process follow-up.

## C1 — Concurrency on real Postgres (the item the in-memory fakes could not prove)

The unit suite simulates the DB with an in-memory `FakeConn`, so the no-oversell
guarantee under a genuine row lock had never been exercised against a real engine. This
gate closes that gap.

**Setup (isolated, production untouched):** on the VPS docker host, an ephemeral
`pgvector/pgvector:pg16` container with a throwaway database `zwaf_ctest` (no volume, no
published port). Migrations 001→004 applied with `ON_ERROR_STOP=1`. The race driver runs
in an ephemeral `python:3.12-slim` container on the same docker network, importing the
**real** `zwaf.memory.inventory_store`. Each `reserve_inventory` call opens its **own**
asyncpg connection, so two checkouts genuinely race for the last unit. All containers
were removed afterward.

**Result — 30/30 runs PASS.** Every race for `on_hand=1`:
- `sorted(statuses) == ["reserved", "unavailable"]` — exactly one winner
- `reserved_qty == 1`
- `available == 0` — **never negative** (no oversell)
- exactly **1** `active` reservation row

This is the behavioral proof of the core mechanism: the conditional
`UPDATE inventory_items SET reserved_qty = reserved_qty + $qty WHERE available >= $qty`
holds the row lock until commit, forcing the second transaction to re-evaluate
availability under READ COMMITTED and correctly fail closed.

## Migrations apply cleanly — PASS (bonus)

Beyond C1, this exercised the **first real application of migration 004**. All four
migrations applied in order against a clean pgvector DB with `ON_ERROR_STOP=1` and no
errors — including the `inventory_items_no_oversell` CHECK constraint, the three new
tables, and the idempotent stock seed. This de-risks step 2 of the go-live runbook.

## Check summary

### Code review — PASS
`inventory_store.py` is self-contained (stdlib + lazy `asyncpg`; no project imports).
Reservation, release, webhook confirm/release/refund-review, expiry sweep, status and
audited manual adjustment are cleanly separated; `_conn`-suffixed helpers operate inside
a caller transaction so the webhook confirms/releases stock in the same tx as the payment
event. Idempotency via `UNIQUE(order_id)` + existing-reservation short-circuit;
`manual_adjustment` requires a non-empty reason and `created_by` (raises `ValueError`).

### Unit tests — PASS
184 passed (tests/unit), +48 over baseline. ruff "All checks passed"; mypy exit 0.

### Concurrency (Postgres) — PASS
30/30 as above. See `tests/integration/test_inventory_concurrency.py` (the gate driver
mirrors it and stresses it 30×).

### Acceptance criteria — Met
| AC | Status | Evidence |
|---|---|---|
| Reserve stock atomically before the Asaas link; block link when unavailable | Met | `payment.py` reserves first; on `unavailable`/`error` returns the unavailable message, no Asaas call |
| Two concurrent checkouts for the last unit never oversell | Met | **C1: 30/30, available never negative** |
| Idempotent reservation per order; webhook confirm/release/refund-review idempotent | Met | unit suite (confirm/cancel/refund/duplicate) + `UNIQUE(order_id)` |
| TTL + release-expired sweep returns stranded stock | Met | `release_expired` + `inventory_cli release-expired` (cron in runbook) |
| Audited movements ledger | Met | `inventory_movements` append-only; every op records a movement |

### No regressions — PASS
184/184 green; existing payment/webhook/tenant-isolation tests unchanged.

### Security — PASS
Parameterized SQL throughout; no secrets/PII logged; DB CHECK constraint
(`reserved_qty + committed_qty <= on_hand_qty`) is the last-resort oversell backstop.

### Docs — PASS
Story-034 File List, AC and DoD accurate. Go-live runbook documents manual migration 004
and the release-expired cron.

## Recommendation
Ship. The one open item (C2 / CodeRabbit) is a non-blocking second-opinion pass to run
when a WSL+vault environment is available. The deploy itself is gated separately on the
go-live runbook (the code is merged but not yet deployed to the VPS — `/opt/zwaf` runs the
pre-034 build).
