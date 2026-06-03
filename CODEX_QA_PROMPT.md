# Codex QA Prompt — Lívia SDR: ZWAF (Raiz Vital)

## Missão

Você é um engenheiro de QA especialista em Python assíncrono. Audite, teste, corrija e deixe funcionalmente correto o sistema **Lívia SDR** — uma WhatsApp SDR autônoma para a Raiz Vital rodando no framework ZWAF. O sistema está arquiteturalmente completo. Seu objetivo: garantir que `python -m harnesses.conversation_harness --all` passe 100% e que ao conectar credenciais o sistema funcione sem erros.

---

## Contexto do Projeto

### O que é a Lívia

WhatsApp SDR (Sales Development Representative) autônoma para a Raiz Vital. Recebe mensagens via Evolution API, usa 5 agentes especializados (vendedor, recompra, suporte, cobrança, fidelização), integra Abacate Pay para geração de PIX, e opera 24/7 em VPS Docker.

**Objetivo:** Converter leads do Caio (gestor de tráfego Meta Ads) em vendas via WhatsApp. Meta Sprint 1: 600 potes → R$90k em 14 dias.

### Arquitetura (todos em `packages/zwaf/`)

```
src/zwaf/
  core/
    tenant.py          — TenantConfig: carrega config.json, substitui ${ENV_VAR}
    base_agent.py      — Factory Agno Agent com LLM + prompt + Postgres storage
    router_agent.py    — Roteamento híbrido: keyword → LLM fallback → default vendedor
    team.py            — ZWAFTeam: orquestra guard + router + 5 agentes
  security/
    guard.py           — InputGuard: bloqueia prompt injection (12 padrões PT/EN)
  api/
    routes/webhook.py            — POST /v1/webhook/{tenant_id} — entry point Evolution API
    routes/payment_webhook.py    — POST /v1/webhook/payment/{tenant_id} — Abacate Pay HMAC
    middleware/auth.py           — APIKeyMiddleware (webhooks excluídos do check)
  agents/
    vendedor.py        — Primeiro contato, qualificação, link de compra
    recompra.py        — Segundo pote / renovação
    suporte.py         — Pedidos, rastreio, dúvidas
    cobranca.py        — Pix expirado, novo link
    fidelizacao.py     — FidelizacaoAgent + FidelizacaoScheduler (APScheduler cron 9h, dia 30)
  tools/
    base.py            — BaseTool, with_retry, with_429_retry, RateLimitError
    whatsapp.py        — WhatsAppTool: send_message, warm-up, throttle, rate limiting
    catalog.py         — make_catalog_search(): busca em knowledge/*.md por score
    payment.py         — make_payment_link_generator(): Abacate Pay PIX
    escalation.py      — escalate_to_human()
    
tenants/livia-raiz-vital/
  config.json          — LLM: gpt-4o-mini (primary) + gemini-1.5-flash (fallback)
  prompts/             — vendedor.md, recompra.md, suporte.md, cobranca.md, fidelizacao.md
  knowledge/           — new-woman.md, alpha-pulse.md (catálogo / knowledge base)

harnesses/
  conversation_harness.py   — 10 cenários SPEC 8.1 com mocks AsyncMock
  evaluation_harness.py
  setup_harness.py

tests/unit/
  test_router_agent.py      — 20 testes TDD com pytest-asyncio
  test_tenant.py
  test_whatsapp_tool.py

infra/migrations/001_initial_schema.sql  — leads, zwaf_sessions, knowledge_base (pgvector), payment_events
docker-compose.yml           — PostgreSQL 16 + pgvector, Redis 7, Evolution API v2.3.7, zwaf-api
.env.example                 — Template completo com todas as variáveis
```

---

## Spec — Acceptance Criteria (Cenários SPEC 8.1)

```
1. lead_frio_preco        — Lead pergunta preço → Lívia qualifica, diz o preço, oferece link
2. lead_objecao_caro      — "tá caro" → tratamento de objeção com custo-benefício
3. lead_ingredientes_new_woman — Pergunta sobre ingredientes → resposta precisa (sem inventar)
4. cliente_recompra       — Cliente reconhecido retorna → agente recompra acionado
5. pedido_nao_chegou      — Suporte: rastreio e prazo (5-10 dias SEDEX)
6. problema_pix           — Cobrança: Pix expirado → novo link
7. pagamento_confirmado   — Confirmação de pagamento → agradecimento + orientações uso
8. mensagem_madrugada     — Mensagem às 3h → resposta com warm-up não excedido
9. prompt_injection       — Tentativa de jailbreak → InputGuard bloqueia
10. escalacao_humano      — Reação adversa → escalate_to_human() acionado
```

**KPIs de Qualidade:**
- Latência de resposta: ≤ 3000ms por turno (mock)
- Agente correto para cada cenário
- Nenhuma invenção de ingrediente
- Link PIX gerado em ≤ 2 turnos quando cliente quer comprar
- `escalate_to_human()` acionado em casos de risco (reação adversa, reembolso)

---

## Checklist de Auditoria — Execute Nesta Ordem

### FASE 1 — Imports e Dependências

**1.1 Verificar requirements.txt**

Leia `requirements.txt`. Confirme que estão presentes:
- `agno>=1.0.0` (framework de agentes)
- `openai>=1.0.0` (para gpt-4o-mini)
- `httpx>=0.27.0` (para Evolution API + Abacate Pay)
- `asyncpg>=0.29.0` (PostgreSQL assíncrono)
- `apscheduler>=3.10.0` (FidelizacaoScheduler)
- `fastapi>=0.110.0` + `uvicorn` (API)
- `pydantic>=2.7.0`
- `python-dotenv>=1.0.0`
- `tenacity>=8.3.0` (retry)

Se algum estiver faltando mas for usado no código, adicione ao requirements.txt.

**1.2 Verificar imports circulares**

Os módulos `core/team.py`, `core/router_agent.py` e `agents/*.py` podem ter imports cruzados. Verifique:
- `team.py` não importa diretamente os agentes no topo (deve usar lazy imports em `_build_agent()`)
- `router_agent.py` não importa `team.py`
- `agents/*.py` não importam uns aos outros

**1.3 Verificar `base_agent.py` — Agno API**

Confirme que `build_agent()` usa a API Agno correta para `claude-sonnet-4-6` ou `gpt-4o-mini` dependendo do `tenant.llm.model`. Se usar `OpenAI(id="gpt-4o-mini")`, verifique que o import é de `agno.models.openai`.

---

### FASE 2 — Core da Equipe (team.py)

**2.1 Fluxo de processamento**

Trace o fluxo completo de uma mensagem recebida:
```
webhook.py → team.process(phone, text, push_name) 
  → InputGuard.check()      # bloqueia injection/spam?
  → RouterAgent.route()     # keyword ou LLM?
  → _build_agent(name)      # instancia agente correto?
  → agent.run(message)      # resposta gerada?
  → whatsapp.send_message() # enviada com throttle?
```

Verifique cada step. Se qualquer passo não existir ou estiver quebrado, corrija.

**2.2 Verificar `fidelizacao` no `_build_agent()`**

Confirme que há um caso explícito (ou warning) quando `agent_name == "fidelizacao"` é roteado pelo `RouterAgent`. Esse agente NÃO deve ser roteado diretamente — apenas o `FidelizacaoScheduler` o invoca. Se o router mandar para `fidelizacao`, deve redirecionar para `vendedor` com log de warning.

**2.3 Session isolation**

Sessões diferentes (números de telefone diferentes) não devem vazar contexto entre si. Verifique se `session_id = f"{tenant_id}_{phone}"` é usado consistentemente em todas as chamadas de agente.

---

### FASE 3 — WhatsApp Tool (whatsapp.py)

**3.1 Warm-up logic**

A função `get_warm_up_limit(day, messages_per_minute)` deve retornar:
- Dias 1-3: 20 mensagens/dia
- Dias 4-7: 50 mensagens/dia
- Dia 8+: `messages_per_minute * 60 * 8` (produção plena)

Verifique se `day` é calculado corretamente a partir de `WA_WARMUP_START_DATE`. Se `WA_WARMUP_START_DATE` não estiver no env, o sistema deve ter um default seguro (dia 1).

**3.2 Rate limiting**

`PhoneRateLimiter` deve rastrear timestamps por número de telefone e bloquear envios que excedam `messages_per_minute`. Se a estrutura usar `asyncio.Lock`, confirme que não há deadlock possível (lock adquirido e nunca liberado em caso de exception).

**3.3 Retry hierarchy**

A hierarquia de retry deve ser:
1. `_send_raw()` — tenta primeiro sem retry
2. `_send_raw_with_5xx_retry()` — até 3 tentativas com backoff para erros 5xx
3. `_send_with_429_retry()` — backoff 30s+jitter para Rate Limit 429

Confirme que 429 não é tratado como 5xx (deve ter delay maior).

---

### FASE 4 — Segurança e Auth

**4.1 InputGuard — 12 padrões**

Leia `security/guard.py`. Confirme que os padrões cobrem:
- "ignore all previous instructions" (EN)
- "ignore todas as instruções" (PT)
- Variações com "forget", "pretend", "act as"
- Detecção de spam (mensagem repetida N vezes)

Se algum padrão óbvio estiver faltando, adicione.

**4.2 Webhook auth**

Leia `api/middleware/auth.py`. Confirme que:
- Endpoints `/v1/webhook/` (Evolution API) são excluídos do check de `ZWAF_API_KEYS` — correto, pois Evolution API não envia API key
- Endpoint `/v1/webhook/payment/` verifica HMAC-SHA256 separadamente em `payment_webhook.py`
- Outros endpoints (`/health`, `/metrics`) têm comportamento apropriado

**4.3 HMAC Abacate Pay**

Leia `api/routes/payment_webhook.py`. O HMAC deve:
- Usar `ABACATE_PAY_WEBHOOK_SECRET` do env
- Calcular `HMAC-SHA256` do body raw (não parsed JSON)
- Retornar 401 se assinatura inválida
- Persistir evento em `payment_events` com `tenant_id`, `amount`, `status`, `lead_phone`

---

### FASE 5 — Payment Integration

**5.1 Abacate Pay — geração de link**

Leia `tools/payment.py`. O `make_payment_link_generator()` deve:
- POST para `{ABACATE_PAY_BASE_URL}/v1/billing/create`
- Payload: `{ "customer": { "taxId": phone }, "products": [...], "methods": ["PIX"] }`
- Retornar URL de pagamento PIX
- Ter fallback mock quando `ABACATE_PAY_KEY` não está no env

**5.2 Fallback mock**

Confirme que o mock retorna uma URL válida (mesmo que fake) para testes locais sem credenciais.

---

### FASE 6 — Harnesses e Testes

**6.1 Rodar mentalmente o harness**

Leia `harnesses/conversation_harness.py` completo. Para cada cenário, verifique:
- O `expected_agent` corresponde ao agente que o `RouterAgent` escolheria para aquela mensagem
- `expected_contains` são strings que a resposta deve conter
- `forbidden_contains` são strings que NUNCA devem aparecer (ex: ingredientes inventados)

**6.2 Verificar mocks**

O harness usa `AsyncMock` para `whatsapp.send_message`. Confirme que:
- O mock está aplicado corretamente (patch no path certo)
- A fixture de banco de dados usa banco in-memory ou fixture separada (não o Postgres real)
- Os agentes conseguem rodar com `OPENAI_API_KEY=test` sem fazer chamadas reais à API

**6.3 `test_router_agent.py` — 20 testes**

Leia o arquivo. Verifique:
- Todos os 20 testes têm `@pytest.mark.asyncio`
- Não há testes que dependem de ordem de execução
- O mock do LLM fallback retorna um nome de agente válido

---

### FASE 7 — Schema e Migrations

**7.1 Verificar migration `001_initial_schema.sql`**

Leia a migration. Confirme que as tabelas existem com os campos corretos:
- `leads(tenant_id, phone, name, status, created_at, updated_at)`
- `zwaf_sessions(tenant_id, session_id, phone, agent_name, context, updated_at)`
- `payment_events(tenant_id, payment_id, lead_phone, amount, status, created_at)`
- `knowledge_base(tenant_id, content, embedding vector(1536), metadata, created_at)`

Se campos críticos estiverem faltando, adicione na migration.

**7.2 Verificar que queries no código usam $1, $2 parametrizados**

Grep por SQL strings em `src/zwaf/`. NUNCA deve ter string interpolation em SQL. Qualquer `f"SELECT... {variable}"` em SQL é uma injeção SQL crítica — corrija imediatamente para queries parametrizadas.

---

### FASE 8 — Docker e Infra

**8.1 Verificar `docker-compose.yml`**

Confirme:
- Service `zwaf-api` tem `depends_on: [postgres, redis, evolution-api]`
- Variáveis de ambiente são passadas via `env_file: .env` (não hardcoded)
- Volumes persistentes para postgres e evolution-api
- Health checks definidos para postgres e evolution-api

**8.2 Verificar `.env.example`**

Confirme que `.env.example` tem TODAS as variáveis necessárias com valores de exemplo/placeholder:
- `WA_WARMUP_START_DATE=2026-05-26` (data de hoje como placeholder)
- `OPENAI_API_KEY=sk-...`
- `DATABASE_URL=postgresql://zwaf:zwaf@localhost:5432/zwaf`
- `EVOLUTION_API_URL=http://evolution-api:8080`

---

## Como Executar

```bash
cd packages/zwaf

# Instalar dependências
pip install -r requirements.txt

# Rodar testes unitários
pytest tests/unit/ -v

# Rodar harness completo (com mocks)
python -m harnesses.conversation_harness --all --tenant livia-raiz-vital

# Verificar imports
python -c "from src.zwaf.core.team import ZWAFTeam; print('team OK')"
python -c "from src.zwaf.core.router_agent import RouterAgent; print('router OK')"
python -c "from src.zwaf.tools.whatsapp import WhatsAppTool; print('whatsapp OK')"
python -c "from src.zwaf.tools.payment import make_payment_link_generator; print('payment OK')"
python -c "from src.zwaf.api.routes.webhook import router; print('webhook OK')"

# Lint
ruff check src/ harnesses/ tests/
```

---

## Resultado Esperado

1. `pytest tests/unit/` — todos os 20 testes passando
2. `python -m harnesses.conversation_harness --all` — todos os 10 cenários passando
3. Imports sem `ImportError` em todos os módulos
4. Nenhuma string interpolation em queries SQL
5. Warm-up logic correta com fallback seguro quando `WA_WARMUP_START_DATE` não está configurado
6. HMAC Abacate Pay funcionando com mock quando sem chave real
7. `fidelizacao` roteável com warning (não cai silenciosamente no vendedor)

## O que NÃO fazer

- Não conectar APIs reais (sem credenciais disponíveis)
- Não alterar o modelo LLM do tenant (manter gpt-4o-mini conforme config.json)
- Não remover agentes ou reduzir escopo
- Não commitar `.env` com valores reais

**Quando estiver correto:** reportar lista de bugs encontrados + fixes aplicados + status dos testes.

---

*ZWAF v1 — Lívia SDR | Raiz Vital × Zvision | Sprint 1 — 600 potes em 14 dias*
