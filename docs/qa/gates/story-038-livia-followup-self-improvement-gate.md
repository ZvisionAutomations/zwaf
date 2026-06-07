---
storyId: STORY-038
verdict: PASS
reviewer: Litmus (@quality-gate)
date: 2026-06-07
track: Complex (score 18) - conversion / governance / PII focus
branch: caio/feat/story-038-livia-followup-self-improvement
checks:
  unit_tests: PASS
  lint: PASS
  dry_run_harness: PASS
  pii_secret_scan: PASS
  acceptance_criteria: PASS
  docs: PASS
evidence:
  unit_suite: "209 passed, 1 warning - tests/unit"
  lint: "ruff check --no-cache src tests harnesses - All checks passed"
  dry_run: "python -m harnesses.livia_followup_story_038 --dry-run - all checks PASS"
  warning: "StarletteDeprecationWarning from fastapi.testclient; non-blocking"
issues:
  - severity: low
    category: environment
    description: >-
      The Google Drive workspace denies writes for pytest tmp_path/cache in some
      local subdirectories. Unit tests passed with an explicit basetemp under the
      Windows temp directory and pytest cache disabled.
    recommendation: >-
      Keep using --basetemp C:\Users\Suporte\AppData\Local\Temp\zwaf-pytest-unit-story038
      -p no:cacheprovider for this workstation, or fix workspace temp permissions.
---

# QA Gate Handoff - Story 038: Livia Follow-up + Supervised Self-Improvement Loop

**Verdict: PASS.** `@quality-gate` re-reviewed the resolved concerns and approved
the story.

## Scope Implemented

- Deterministic lead temperature classification: `hot`, `warm`, `cold`, `risk`.
- Follow-up policy by temperature and stage with approved templates and hard stops.
- PII-safe funnel event builder with hashed session identifiers.
- Supervised improvement queue with `suggested`, `approved`, `rejected`, `promoted`.
- Review/promotion log for improvement candidates.
- Governance/commercial notifications via env-only recipients:
  `OPERATOR_PERSONAL_WHATSAPP` and `FERNANDO_WHATSAPP`.
- Commercial daily report formatter/sender without customer PII.
- CLI-first dry-run harness for the required Story 038 scenarios.
- Existing social proof KB already present at
  `docs/kb/livia-social-proof-authorized.md` with fields and migration gates.
- Removed hardcoded personal escalation phone fallback; `ESCALATION_PHONE` is now
  env-only.

## Commands Executed

```powershell
cd packages/zwaf

.venv\Scripts\pytest.exe tests\unit -q --basetemp C:\Users\Suporte\AppData\Local\Temp\zwaf-pytest-unit-story038 -p no:cacheprovider

.venv\Scripts\ruff.exe check --no-cache src tests harnesses

.venv\Scripts\python.exe -m harnesses.livia_followup_story_038 --dry-run
```

## Results

- Unit tests: `209 passed, 1 warning`.
- Lint: `All checks passed`.
- Story 038 harness: all checks `PASS`.

## Quality-Gate Concerns Addressed

Initial `@quality-gate` review returned `CONCERNS` and the developer pass fixed
the findings before re-review:

- Commercial report action fields now redact likely names, addresses and other
  free-form PII before rendering.
- `tests/unit/test_livia_followup_story_038.py` covers the reported case:
  `Maria Silva - Rua das Flores 123` is not emitted, while non-PII identifiers
  such as `lead-session-42` remain allowed.
- Escalation notification now hashes the lead phone in `session_id` and logs
  only `lead_tail`, not the raw lead phone.
- `.env.example` no longer uses `sk-*`/`pk-*` placeholder values and leaves
  WhatsApp number placeholders blank.

Re-run after fixes:

```powershell
.venv\Scripts\pytest.exe tests\unit -q --basetemp C:\Users\Suporte\AppData\Local\Temp\zwaf-pytest-unit-story038 -p no:cacheprovider
# 209 passed, 1 warning

.venv\Scripts\ruff.exe check --no-cache src tests harnesses
# All checks passed

.venv\Scripts\python.exe -m harnesses.livia_followup_story_038 --dry-run
# all checks PASS
```

## Acceptance Mapping

| Acceptance criterion | Developer evidence |
|---|---|
| Hot lead schedules follow-ups and never exceeds 5 contacts | `test_hot_followup_post_offer_schedules_max_five` + dry-run `hot_abandoned_post_offer_max_5` |
| Warm/cold do not receive hot sequence | `test_warm_cold_and_resistant_limits` + dry-run warm/cold checks |
| Opt-out and medical risk stop commercial follow-up | `test_followup_stops_for_opt_out_and_medical_risk` + dry-run risk/opt-out checks |
| Funnel events contain no raw PII and use hash/session grouping | `test_funnel_event_uses_hash_and_strips_pii` |
| Improvement candidates do not alter live prompt/template without approval | `test_improvement_candidates_do_not_promote_without_approval` |
| Operator/Fernando recipients are local env only | `test_notifications_use_local_env_without_versioned_phone`, `test_commercial_report_sends_to_fernando_env` |
| Commercial daily report exposes aggregates only | `test_commercial_report_is_aggregate_and_redacts_pii` |
| Social proof remains pending until approval and has DB migration gates | `docs/kb/livia-social-proof-authorized.md` |

## Files for Review

- `.env.example`
- `harnesses/livia_followup_story_038.py`
- `src/zwaf/conversion/followup.py`
- `src/zwaf/conversion/funnel_events.py`
- `src/zwaf/conversion/self_improvement.py`
- `src/zwaf/reporting/commercial_report.py`
- `src/zwaf/tools/notifications.py`
- `src/zwaf/tools/escalation.py`
- `tests/unit/test_livia_followup_story_038.py`
- `tests/unit/test_livia_checkout_requirements.py`

Also reviewed/fixed for lint cleanliness:

- `harnesses/conversation_harness.py`
- `harnesses/evaluation_harness.py`
- `harnesses/report_harness.py`
- `harnesses/setup_harness.py`
- `harnesses/throttle_harness.py`
- `harnesses/warmup_harness.py`

## Residual Risk

- No production follow-up scheduler/send loop is activated by this story.
- Persistence for improvement candidates remains in-memory; DB persistence is a
  future step after governance approval.
- Real WhatsApp delivery depends on local/server env values and Evolution API
  configuration.
- The repo has unrelated pre-existing local modifications in agent, team, tenant
  config, and prompt files. They were not reverted.

## Final Quality-Gate Verdict

PASS. The developer-side checks are green and `@quality-gate` confirmed the PII
concerns were addressed. Remaining risk is non-blocking: free-form commercial
report fields rely on conservative redaction heuristics, so production callers
should continue sending aggregates and session IDs rather than customer details.
