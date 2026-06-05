---
storyId: STORY-032
verdict: PASS
reviewer: Litmus (@quality-gate)
date: 2026-06-05
track: Brownfield Medium Risk (Standard — 7 checks) — security focus
branch: caio/feat/story-032-audio-download-hardening
commit: 8248d13
checks:
  code_review: PASS
  unit_tests: PASS
  acceptance_criteria: PASS
  no_regressions: PASS
  performance: PASS
  security: PASS
  docs: PASS
evidence:
  unit_suite: "161 passed (tests/unit, raw pytest)"
  focused_suite: "24 passed (-k 'download or url or ssrf or audio or evolution')"
  ruff: "All checks passed! (src tests)"
  mypy: "Success — exit 0 (1 pre-existing informational annotation-unchecked note in tools/whatsapp.py:89, out of scope)"
  httpx_version: "0.28.1"
  redirect_default: "AsyncClient.follow_redirects default = False; .stream() inherits via UseClientDefault — redirects NOT followed"
issues:
  - severity: low
    category: security
    description: >-
      TOCTOU / DNS-rebinding residual: _validate_download_url resolves the host via
      socket.getaddrinfo and checks every returned address, but httpx then reconnects
      by hostname (independent resolution). A hostile resolver could return a public IP
      during validation and an internal IP at connect time. This is the documented
      accepted residual in the story (mitigated by the optional host allowlist +
      upstream instance-auth). Not introduced by this story.
    recommendation: >-
      For full closure, resolve once and pin the connection to the validated IP
      (custom httpx transport / connect to IP with Host header), or front media
      fetching with an egress proxy. Track as future hardening; non-blocking.
  - severity: low
    category: tests
    description: >-
      No explicit unit test asserts redirect behavior (e.g. a 3xx response from an
      allowed host is not transparently followed to an internal IP). Behaviorally safe
      because follow_redirects defaults to False and raise_for_status() does not raise
      on 3xx (a redirect simply yields the small redirect body, never reaching the
      Location target). A regression test pinning follow_redirects=False would harden
      against an accidental future flip of that default.
    recommendation: >-
      Add a test that builds a 302 response from an allowed host and asserts the code
      never connects to the redirect target (or asserts the client is constructed with
      follow_redirects=False).
---

# QA Gate — Story 032: Audio Download Hardening (SSRF + coverage)

**Verdict: PASS** (2 low-severity, non-blocking residuals documented as tech debt)

Security-focused re-gate of the issues #3 (SSRF / byte-cap) and #4 (URL/Evolution
test coverage) carried over from the story-019 gate. Scope: `src/zwaf/audio/transcription.py`
(SSRF guard + streaming cap) plus tests and `.env.example`. No production code was
modified by this review.

## Primary focus — SSRF guard correctness

### 1. Redirect bypass (CRITICAL vector) — NOT exploitable
The download uses `httpx.AsyncClient(...).stream("GET", url)` with **no**
`follow_redirects` override. Verified against the installed runtime (httpx 0.28.1):
`AsyncClient.follow_redirects` defaults to **False**, and `.stream()` inherits the
client default via `UseClientDefault`. Therefore a 302 from an allowed host is **not**
followed to `http://169.254.169.254/...` or any internal IP. Additionally,
`response.raise_for_status()` does not raise on 3xx, so a redirect response simply
yields its (small) redirect body and is returned as bytes — the `Location` target is
never connected to. **No SSRF-via-redirect.** Downgraded from the worst-case HIGH/CRITICAL
to a LOW test-coverage note (no regression test pins `follow_redirects=False`).

### 2. Resolution vs connection (TOCTOU / DNS rebinding) — LOW, accepted
`_validate_download_url` resolves with `getaddrinfo` and iterates **all** returned
sockaddrs, rejecting if **any** is internal (loop at L119-126) — not just the first
address. httpx then reconnects by hostname, leaving the documented rebinding residual.
Consistent with the story's accepted LOW (allowlist + upstream instance-auth mitigate).
Logged as issue above.

### 3. Blocked-range coverage — comprehensive (IPv4 + IPv6)
`_is_blocked_ip` blocks `is_private | is_loopback | is_link_local | is_reserved |
is_multicast | is_unspecified`. Empirically verified on the runtime:
- IPv6 `::1` (loopback), `fc00::/7` (ULA, private), `fe80::` (link-local), `::`
  (unspecified), `64:ff9b::` (NAT64, reserved) → all **blocked**.
- IPv4-mapped IPv6 `::ffff:127.0.0.1`, `::ffff:169.254.169.254`, `::ffff:10.0.0.5`
  → all **blocked** (Python `ipaddress` classifies mapped addresses by the embedded
  IPv4). This closes the classic mapped-address bypass.
- Public `2001:4860:4860::8888` → correctly **allowed**.
The `ip_address()` parse is wrapped: an unparseable sockaddr returns `blocked_url_ip`
(fail-closed).

### 4. Byte cap during streaming — correct
`_download_audio_url` enforces the cap **inside** the `aiter_bytes()` loop
(L323-327): each chunk is appended and `len(buffer) > max_bytes` aborts with
`media_too_large` before the full body is buffered. There is no full read before the
check. Covered by `test_download_audio_url_aborts_when_exceeding_max_bytes`.

### 5. Scheme restriction — correct
`_validate_download_url` returns `blocked_url_scheme` for anything other than
`http`/`https`; `file://`, `ftp://`, `gopher://` are rejected before any network or
DNS work. Covered by `test_download_audio_url_rejects_non_http_scheme` (ftp). Note the
caller `load_audio_content` also pre-filters on `http(s)` prefix, so the guard is
defense-in-depth.

## 7-check summary

### Code review — PASS
SSRF guard is well-factored (`_validate_download_url`, `_allowed_download_hosts`,
`_is_blocked_ip`). Validation runs before any socket connect; rejection reasons are
returned as typed codes and logged as `{reason}` only. Fail-closed on DNS error
(`blocked_url_dns`) and unparseable IP (`blocked_url_ip`). Cleanups from scope applied:
`transcribe_audio` fallback simplified to a single return (L210-217), no dead branch.

### Unit tests — PASS
- Full suite: **161 passed** (raw pytest, tests/unit). +11 vs story-019 baseline (150).
- Focused (`download or url or ssrf or audio or evolution`): **24 passed**.
- New vectors covered: allowlist hit/miss, private/loopback/link-local IP
  (127.0.0.1, 169.254.169.254, 10.0.0.5 via parametrize), non-http scheme, streaming
  byte-cap abort, timeout → fallback, success streaming, and Evolution
  `getBase64FromMediaMessage` success + failure/fallback. All use mocked httpx — no
  real network.
- Gaps (LOW): no explicit redirect test; no explicit IPv6/mapped-address test (behavior
  verified manually in this gate, not pinned by a unit test).

### Acceptance criteria — all Met
| AC | Status | Evidence |
|---|---|---|
| Reject host outside allowlist + private/loopback IP, no external request | **Met** | `test_download_audio_url_rejects_host_not_in_allowlist` + `..._rejects_private_or_loopback_ip` assert `client.stream.assert_not_called()` |
| Abort when exceeding TRANSCRIPTION_MAX_BYTES **during** streaming | **Met** | `test_download_audio_url_aborts_when_exceeding_max_bytes` → `media_too_large`; cap is inside `aiter_bytes` loop |
| Tests cover blocked host, byte overflow, timeout, getBase64FromMediaMessage success+fallback | **Met** | 24 focused tests incl. both Evolution paths |
| Fallback never blocks webhook / never calls agent with empty text | **Met** | Inherited from story-019 (webhook offload + outer try/except); full suite green, no regression |
| ruff + mypy clean; unit suite green | **Met** | ruff "All checks passed"; mypy exit 0; 161 passed |
| No secret/PII logged | **Met** | logs carry only `{reason}`, `{error_type}` — no URL, host, bytes, or base64 |

### No regressions — PASS
161/161 green, +11 over the story-019 baseline (150) and no failures/skips. Existing
audio, webhook and reporting tests unchanged.

### Performance — PASS
Streaming with early abort caps memory at `TRANSCRIPTION_MAX_BYTES` (the story-019
"buffer-everything-first" caveat is now closed). Per-call timeout via
`TRANSCRIPTION_TIMEOUT_SECONDS`. All I/O async.

### Security — PASS
SSRF guard (scheme + optional host allowlist + all-addresses internal-IP block),
fail-closed DNS/parse handling, IPv6 + mapped-address coverage, streaming byte cap.
No secrets in `src/zwaf/audio/`; logs free of PII/URLs. Residual TOCTOU/rebinding is
the documented accepted LOW (allowlist + instance-auth).

### Docs — PASS
`.env.example` documents `TRANSCRIPTION_URL_ALLOWED_HOSTS` (L49-51) with an explicit
note that empty = no hostname restriction and that private/loopback/link-local IPs are
always blocked. Story-032 File List, AC and DoD are accurate; DoD item "QA gate re-run
PASS" is satisfied by this gate.

## Recommendation
Two non-blocking LOW follow-ups: (a) add a regression test pinning
`follow_redirects=False` / asserting redirect targets are never connected; (b) longer
term, pin the connection to the validated IP to close the TOCTOU/rebinding residual.
Neither blocks merge.
