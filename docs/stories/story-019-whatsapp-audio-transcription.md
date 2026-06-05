# Story 019 - Transcricao de audio WhatsApp

**Status:** Ready
**Sprint:** Sprint 19
**Epic:** ZWAF - Experiencia WhatsApp multimodal
**Criado:** 2026-06-05
**Validado:** 2026-06-05 (Sync/Axis local - escopo tecnico aprovado para desenvolvimento)
**Track:** Brownfield Medium Risk

## Contexto

Leads enviam audio no WhatsApp com frequencia. Hoje o webhook da Evolution API ignora
mensagens sem texto (`conversation` ou `extendedTextMessage.text`), entao audios nao entram
no roteamento nem nos agentes.

A primeira versao deve transcrever audios de WhatsApp usando Groq Whisper como provider
principal, mantendo fallback seguro quando transcricao ou download de midia falhar.

## Escopo

### IN

- Detectar `audioMessage` e `ptt` no payload `messages.upsert` da Evolution API.
- Obter bytes do audio a partir de payload base64 direto, URL de midia ou endpoint interno
  da Evolution API.
- Transcrever audio via Groq Speech-to-Text (`whisper-large-v3-turbo` por default).
- Encaminhar o texto transcrito para o mesmo fluxo existente de `team.process`.
- Nao logar audio bruto, base64, URLs assinadas, telefone completo ou transcricao completa.
- Fallback: se provider estiver desabilitado/sem chave/falhar, responder sem quebrar o webhook.
- Adicionar envs documentadas em `.env.example`.
- Cobrir por testes unitarios sem chamada externa real.

### OUT

- Speech-to-speech em tempo real.
- Envio de resposta em audio.
- Mudancas em estoque, pagamentos ou frete.
- Chamada real para Groq/OpenAI em testes.
- Dependencia de Docker local.

## Acceptance Criteria

```gherkin
DADO um webhook da Evolution API com mensagem de audio e base64 no payload
QUANDO o webhook processa o evento
ENTAO o audio e transcrito
  E o texto transcrito e enviado ao fluxo existente do agente

DADO um webhook com audio e provider de transcricao desabilitado
QUANDO o webhook processa o evento
ENTAO o webhook retorna accepted
  E o agente responde com fallback pedindo texto

DADO que a transcricao falha por timeout ou erro do provider
QUANDO o webhook processa o evento
ENTAO nenhum payload sensivel e logado
  E o lead recebe uma mensagem de fallback

DADO uma mensagem de texto normal
QUANDO o webhook processa o evento
ENTAO o comportamento existente permanece inalterado
```

## Dev Technical Guidance

- Novo modulo sugerido: `src/zwaf/audio/transcription.py`.
- Provider default: `TRANSCRIPTION_PROVIDER=groq`.
- Endpoint Groq: `POST https://api.groq.com/openai/v1/audio/transcriptions`.
- Modelo default: `whisper-large-v3-turbo`.
- Envs:
  - `TRANSCRIPTION_PROVIDER=groq|disabled`
  - `GROQ_API_KEY=`
  - `GROQ_TRANSCRIPTION_MODEL=whisper-large-v3-turbo`
  - `TRANSCRIPTION_TIMEOUT_SECONDS=20`
  - `TRANSCRIPTION_MAX_AUDIO_BYTES=26214400`
- Reusar `httpx`, ja existente em `requirements.txt`.
- Evitar SDK novo nesta story para reduzir superficie e dependencia.

## Tasks / Subtasks

- [x] Criar modulo de transcricao com provider Groq e fallback disabled.
- [x] Estender webhook da Evolution para extrair texto de audio.
- [x] Adicionar testes unitarios para base64, provider disabled, erro de provider e texto normal.
- [x] Documentar envs em `.env.example` sem secrets reais.
- [x] Rodar `pytest -m "not integration and not slow and not harness" -q`.
- [x] Rodar `pytest -q`.
- [x] Rodar `ruff check src tests`.
- [x] Rodar `mypy src`.

## Risk Assessment

- **Risco:** vazar audio/base64/transcricao em logs.
  **Mitigacao:** logs apenas com metadados minimos e tamanhos.
- **Risco:** webhook ficar lento.
  **Mitigacao:** timeout configuravel e fallback.
- **Risco:** formatos diferentes de payload Evolution.
  **Mitigacao:** aceitar base64 direto, URL e fallback para endpoint de midia.
- **Risco:** provider gratuito mudar limites.
  **Mitigacao:** provider configuravel e disabled seguro.

## Definition of Done

- `pytest -q` passa.
- `ruff check src tests` passa.
- `mypy src` passa.
- Testes provam audio transcrito e fallback seguro.
- Nenhum secret real adicionado.
- `.env.example` contem apenas placeholders vazios.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Completion Notes

- Criado modulo `zwaf.audio.transcription` com carregamento de audio por base64, URL de midia ou endpoint interno da Evolution API.
- Implementado provider Groq via `httpx` direto no endpoint OpenAI-compatible, sem dependencia nova de SDK.
- Webhook agora aceita `audioMessage`/`pttMessage`, transcreve em background e envia o texto ao mesmo pipeline de texto.
- Falhas de midia/provider/tamanho/MIME enviam fallback seguro ao lead sem chamar o agente com texto vazio.
- Compose dev/client repassam env vars de transcricao para `zwaf-api`.
- Testes usam mocks/fakes; nenhuma chamada real a Groq/Evolution/OpenAI.

### Debug Log References

- `pytest tests/unit/test_audio_transcription.py tests/unit/test_evolution_webhook.py -q` -> 13 passed
- `pytest -m "not integration and not slow and not harness" -q` -> 151 passed
- `pytest -q` -> 151 passed
- `ruff check src tests` -> All checks passed
- `mypy src` -> Success: no issues found in 52 source files

### File List

- `.env.example`
- `docker-compose.yml`
- `docker-compose.client.yml`
- `docs/stories/story-019-whatsapp-audio-transcription.md`
- `PROGRESS.md`
- `src/zwaf/audio/__init__.py`
- `src/zwaf/audio/transcription.py`
- `src/zwaf/api/routes/webhook.py`
- `tests/unit/test_audio_transcription.py`
- `tests/unit/test_evolution_webhook.py`

### Change Log

| Data | Agente | Acao |
|------|--------|------|
| 2026-06-05 | Sync/Axis | Story criada e validada como Ready local para desenvolvimento |
| 2026-06-05 | Pixel | Implementacao concluida e validada localmente |
