---
storyId: STORY-019
verdict: PASS
reviewer: Litmus (@quality-gate)
date: 2026-06-05
track: Brownfield Medium Risk (Standard — 7 checks)
checks:
  code_review: PASS
  unit_tests: PASS
  acceptance_criteria: PASS
  no_regressions: PASS
  performance: PASS
  security: PASS
  docs: PASS
evidence:
  unit_suite: "150 passed in 17.96s (tests/unit, raw pytest)"
  focused_suite: "30 passed (-k 'audio or webhook or report')"
  ruff: "All checks passed!"
  mypy: "Success — exit 0 (1 informational annotation-unchecked note in tools/whatsapp.py, pre-existing, out of scope)"
issues:
  - severity: low
    category: code
    description: >-
      transcribe_audio() fallback branch (transcription.py L157-162) has a
      redundant pattern: when the fallback provider runs, both the success and
      failure paths return fallback_result, so the inner `if fallback_result.ok`
      is dead logic. Behaviorally correct, but the branch can be simplified to
      `return await _transcribe_with_provider(fallback, audio)`.
    recommendation: "Collapse the redundant if/return into a single return for readability."
  - severity: low
    category: code
    description: >-
      webhook.py L170-172 has duplicated terminal return: both the `if not phone
      or not text` branch and the final line return the identical
      {"status": "ignored", "reason": "no_text_content"} dict. Dead duplication.
    recommendation: "Remove the redundant conditional; keep a single final return."
  - severity: low
    category: security
    description: >-
      _download_audio_url() (transcription.py L248-269) follows a media URL taken
      from the Evolution payload with no host allowlist / SSRF guard and no
      max-bytes cap on the streamed response. The size cap is only enforced later
      in transcribe_audio(), so a hostile/oversized URL can still be fully
      downloaded into memory first. MIME allowlist + timeout are enforced, and the
      payload is instance-authenticated upstream, which lowers exposure.
    recommendation: >-
      Consider validating the download host and enforcing TRANSCRIPTION_MAX_BYTES
      during the download (stream + early abort) in a future hardening story.
  - severity: low
    category: tests
    description: >-
      No unit test exercises the media-URL download path (_download_audio_url) nor
      the Evolution getBase64FromMediaMessage fallback path
      (_load_audio_from_evolution). Covered paths: direct base64, unsupported MIME,
      disabled provider, oversized audio, Groq POST, webhook routing/fallback.
    recommendation: "Add mocked-httpx tests for the URL and Evolution media branches."
  - severity: low
    category: docs
    description: >-
      Story Dev Technical Guidance lists env var TRANSCRIPTION_MAX_AUDIO_BYTES,
      while .env.example documents TRANSCRIPTION_MAX_BYTES. The code accepts both
      (transcription.py L52), so there is no defect — only a doc/naming mismatch.
    recommendation: "Align story text and .env.example on a single canonical name."
---

# QA Gate — Story 019: WhatsApp Audio Transcription

**Verdict: PASS** (5 low-severity, non-blocking issues documented as tech debt)

## 1. Code Review — PASS

`src/zwaf/audio/transcription.py` is well structured and idiomatic:

- Clear separation: descriptor extraction → media loading (3 sources: direct
  base64, media URL, Evolution endpoint) → provider transcription.
- Frozen dataclasses (`AudioContent`, `TranscriptionResult`) give immutable,
  typed contracts. Result-type union (`TranscriptionResult | AudioContent`)
  is consistently checked at call sites.
- Robust error handling: every network call is wrapped; exceptions are caught
  and converted into typed `TranscriptionResult` codes — never propagate raw.
- Config is fully env-driven with safe defaults and `ValueError` guards
  (`_max_audio_bytes`, `_timeout_seconds`).
- **Webhook non-blocking confirmed:** audio is processed via
  `asyncio.create_task(_process_audio_and_respond(...))` — the webhook returns
  `{"status": "accepted"}` immediately and never awaits transcription inline.
  No event-loop blocking: all I/O uses `httpx.AsyncClient` with `await`.
- **Fallback never breaks the webhook:** any failure (no audio, bad MIME,
  oversized, download fail, provider fail, empty transcription) sends a safe
  fallback message and does NOT invoke the agent with empty text. Outer
  try/except in `_process_audio_and_respond` guarantees a fallback even on
  unexpected errors.

Minor readability nits (redundant fallback branch, duplicated webhook return)
logged as low-severity issues above.

## 2. Unit Tests — PASS

- Full suite (raw): **150 passed, 0 failed, 0 skipped, 17.96s**.
- Focused (`audio or webhook or report`): **30 passed**.
- Tests use mocks/fakes only — no real network calls to Groq/Evolution/OpenAI,
  satisfying the story's OUT-of-scope constraint.
- Coverage gap (low): media-URL and Evolution-endpoint media-loading branches
  are not directly tested (see issue above).

> Dev record cites 151 passed; this environment collects 150 (no failures, no
> skips). The 1-test delta is an env-dependent collection difference, not a
> regression.

## 3. Acceptance Criteria — all Met

| AC (Gherkin) | Status | Evidence |
|---|---|---|
| Audio + base64 → transcribed, text routed to existing flow | **Met** | `test_load_audio_content_decodes_direct_base64_audio` + `test_process_audio_routes_transcribed_text` (asserts `team.process` receives transcribed text) |
| Provider disabled → webhook accepted + fallback asks for text | **Met** | `test_transcribe_audio_disabled_provider_does_not_call_network` + `test_process_audio_failure_sends_fallback_without_agent` (agent NOT called, fallback sent) |
| Transcription failure/timeout → no sensitive payload logged + lead gets fallback | **Met** | Provider errors log only `{provider, status/error_type}`; outer except in webhook logs only `error_type`; fallback delivered |
| Normal text message → existing behavior unchanged | **Met** | Text path (`_extract_message`) is checked first and untouched; existing webhook tests green |

## 4. No Regressions — PASS

Full unit suite green (150/150). Pre-existing webhook hardening tests
(unknown tenant 404, invalid instance 403, malformed payload 400, irrelevant
event ignored) still pass. Text-message path is evaluated before audio and is
unchanged.

## 5. Performance — PASS

- Configurable timeout on every HTTP call (`TRANSCRIPTION_TIMEOUT_SECONDS`,
  default 20s, min 1.0).
- Max audio size enforced (`TRANSCRIPTION_MAX_BYTES`, default 25 MiB) before
  hitting the provider in `transcribe_audio`.
- No event-loop blocking: webhook offloads to a background task and all I/O is
  async (`httpx.AsyncClient`).
- Caveat (low): the size cap is not applied during `_download_audio_url`, only
  after — a large remote file is buffered in memory first (see issue).

## 6. Security — PASS

- **Secret scan:** `GROQ_API_KEY=` is empty in `.env.example`; no hardcoded
  keys/tokens in `src/zwaf/audio/` (grep for `gsk_`/`sk-`/`api_key=` → no hits).
- **Input sanitization:** MIME allowlist enforced
  (`TRANSCRIPTION_ALLOWED_MIME_TYPES`); max-bytes guard; base64 decoded with
  `validate=True`; media URL scheme restricted to http/https.
- **LGPD / PII:** logs contain only metadata — provider name, HTTP status,
  `error_type`, and `phone_tail` (last 4 digits) in the webhook. No audio bytes,
  base64, signed URLs, full phone, or transcription text are ever logged.
  Confirmed by reading all `logger.*` calls in transcription.py and webhook.py.
- **ruff:** All checks passed. **mypy:** exit 0.
- Residual low-severity SSRF/no-host-allowlist note on the media-URL download
  (documented above; payload is instance-authenticated upstream).

## 7. Docs — PASS

- Story `docs/stories/story-019-whatsapp-audio-transcription.md` complete:
  scope IN/OUT, Gherkin AC, dev guidance, tasks checked, DoD, Dev Agent Record.
- `.env.example` documents all new envs with empty/placeholder values
  (`TRANSCRIPTION_PROVIDER`, `TRANSCRIPTION_FALLBACK_PROVIDER`,
  `TRANSCRIPTION_LANGUAGE`, `TRANSCRIPTION_TIMEOUT_SECONDS`,
  `TRANSCRIPTION_MAX_BYTES`, `TRANSCRIPTION_ALLOWED_MIME_TYPES`,
  `GROQ_API_KEY` empty, `GROQ_TRANSCRIPTION_MODEL`).
- `PROGRESS.md` records story-019 implementation and S1/S2/S5/S6 results.
- Minor naming mismatch on the max-bytes env var between story and .env.example
  (documented above; code accepts both names, so no defect).

---

## Appendix — Story 031 (light verification)

**Scope:** rename "Sofia" → "Raiz Vital" + SuperFrete `.env.example` hardening.
Story file (outer repo) status: **Done (2026-06-05)**.

| Item | Status | Evidence |
|---|---|---|
| `.env.example` comment rename | **Met** | Line 52 reads `# Relatorio diario Raiz Vital` (no "Sofia") |
| `daily_report.py` docstring | **Met** | L1 docstring: `"""Raiz Vital Daily Report metrics..."""` — no "Sofia" |
| No "Sofia" misnomer in `zwaf.reporting` | **Met** | Grep `Sofia|sofia` in `src/zwaf/reporting/` → 0 matches |
| SuperFrete hardening in `.env.example` | **Met** | `SUPERFRETE_ALLOW_UNSIGNED_WEBHOOKS=false`, `SUPERFRETE_AUTO_CHECKOUT_ENABLED=false`, `SUPERFRETE_WEBHOOK_SECRET=` (empty), HMAC note present |
| Reporting tests still green | **Met** | Covered by full unit suite (150 passed) |

Story-031 verification: **PASS**. The provenance notes about the "Sofia SDR
fork" preserved in guard.py/base.py/whatsapp.py are legitimate lineage, not
misnomers — out of scope per the story's documented scope decision.
