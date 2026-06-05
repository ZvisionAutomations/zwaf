# Story 032 - Hardening do download de áudio (SSRF + cobertura)

**Status:** Ready
**Sprint:** Backlog
**Epic:** ZWAF - Operacao Livia/Caio Raiz Vital
**Criado:** 2026-06-05
**Validado:** 2026-06-05 (@product-lead, GO 9/10)
**Complexidade:** S (track STANDARD) — 1 arquivo de produção principal + testes; risco security médio-baixo (SSRF mitigado por instance-auth)
**Origem:** QA Gate da story-019 (`docs/qa/gates/story-019-audio-transcription-gate.md`) — issues LOW #3 e #4

---

## Contexto

O QA Gate da story-019 (transcrição de áudio WhatsApp) deu **PASS**, com 5 issues
low-severity como tech debt. As duas com peso técnico real — SSRF no download de
mídia e ausência de cobertura para o path de URL — são agrupadas aqui para uma
próxima iteração de hardening. As demais (cleanups) entram como itens menores.

## Escopo

**IN:**
1. **SSRF / cap de bytes no `_download_audio_url`** (`src/zwaf/audio/transcription.py`) — issue #3 (security):
   - Validar o host da URL contra uma **allowlist** (ex.: domínios da Evolution API / mídia esperada), rejeitando IPs privados/loopback/link-local e schemes não-http(s).
   - Aplicar **cap de bytes durante o streaming do download** (hoje o limite só é checado depois, em `transcribe_audio`) — abortar leitura ao exceder `TRANSCRIPTION_MAX_BYTES`.
2. **Cobertura de testes** — issue #4 (tests):
   - Testar o path de **URL de mídia** (`_download_audio_url`) — sucesso, host bloqueado, excesso de bytes, timeout.
   - Testar o endpoint Evolution `getBase64FromMediaMessage` (mock), incluindo falha/fallback.

**Itens menores (oportunísticos, mesmo PR):**
- #1: remover branch de fallback redundante em `transcribe_audio` (L157-162, lógica morta).
- #2: remover `return` duplicado em `webhook.py` (L170-172).
- #5: alinhar nome de env na story-019 doc (`TRANSCRIPTION_MAX_AUDIO_BYTES` → `TRANSCRIPTION_MAX_BYTES`); código já aceita ambos.

**OUT:**
- Mudança no provider de transcrição (Groq) ou no fluxo do webhook fora do download.
- Qualquer alteração de comportamento observável do usuário final.

## Acceptance Criteria

- `_download_audio_url` rejeita host fora da allowlist e URLs para IP privado/loopback (Given URL maliciosa, When download, Then erro sem requisição externa).
- Download aborta ao exceder `TRANSCRIPTION_MAX_BYTES` **durante** o streaming (não só depois).
- Novos testes cobrem: host bloqueado, excesso de bytes em streaming, timeout, e o endpoint `getBase64FromMediaMessage` (sucesso + fallback).
- Fallback de áudio continua não bloqueando o webhook e nunca chama o agente com texto vazio (regressão coberta).
- `ruff check src tests` e `mypy src` limpos; suíte unit verde.
- Nenhum secret/PII logado (mantém o padrão da story-019: só metadados).

## Definition of Done

- [x] Allowlist de host + rejeição de IP privado/loopback em `_download_audio_url`
- [x] Cap de bytes durante streaming do download
- [x] Testes do path de URL + `getBase64FromMediaMessage`
- [x] Cleanups #1, #2, #5 aplicados
- [x] ruff + mypy limpos, suíte verde
- [ ] QA gate re-run PASS

## File List

- `src/zwaf/audio/transcription.py` — SSRF guard (`_validate_download_url`, `_allowed_download_hosts`, `_is_blocked_ip`), cap de bytes via streaming em `_download_audio_url`, cleanup do fallback redundante em `transcribe_audio`.
- `src/zwaf/api/routes/webhook.py` — remoção do `return` duplicado.
- `tests/unit/test_audio_transcription.py` — testes de download por URL (sucesso/allowlist/IP privado/scheme/cap/timeout) + `getBase64FromMediaMessage` (sucesso/fallback).
- `.env.example` — nova env `TRANSCRIPTION_URL_ALLOWED_HOSTS`.
- `docs/stories/story-019-whatsapp-audio-transcription.md` — alinhamento `TRANSCRIPTION_MAX_BYTES`.

## Notas

- Severidade real: **LOW** (mitigado hoje por instance-auth upstream da Evolution), mas SSRF merece fechamento antes de tráfego externo amplo.
- Relacionado: [[story-019-whatsapp-audio-transcription]], gate `docs/qa/gates/story-019-audio-transcription-gate.md`.

## Change Log

| Data | Autor | Mudanca |
|------|-------|---------|
| 2026-06-05 | Litmus (@quality-gate) via Imperator | Story criada a partir das issues #3/#4 do QA Gate da story-019 |
| 2026-06-05 | Axis (@product-lead) | Validada 10-point checklist: GO 9/10 (faltava só estimativa de complexidade, adicionada). Status Draft → Ready. |
