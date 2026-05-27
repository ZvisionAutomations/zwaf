# Livia Raiz Vital - Readiness Report

**Data/hora:** 2026-05-27 12:40:56 -03:00  
**Tenant:** `livia-raiz-vital`  
**Chip WhatsApp:** `+5511967318916`  
**Branch:** `caio/fix/caio-trafego-qa`

## Resultado dos harnesses

| Comando | Resultado |
|---|---|
| `python -m harnesses.conversation_harness --all` | `10/10 (✓ APROVADO)` |
| Import dry-run solicitado | `Todos os imports OK` |
| Build basico de `build_vendedor_agent()` | `Agent` criado com sucesso |
| `python -m harnesses.setup_harness --pre-qr --dry-run` | Nao existe: `unrecognized arguments: --dry-run` |
| `python -m harnesses.setup_harness --pre-qr` | Import/entrada OK; falhou esperado no health check porque a stack local nao esta rodando: `ZWAF API unreachable` |

## Bugs encontrados

| # | Severidade | Status | Arquivo | Achado |
|---|---|---|---|---|
| 1 | CRITICO | FIXED | `harnesses/conversation_harness.py` | Mock `lead_frio_preco` usava `R$150`, preco inexistente no catalogo. Corrigido para `R$165,90` Pix e `R$185,00` cartao. |
| 2 | ALTO | FIXED | `harnesses/conversation_harness.py` | `expected_contains` de preco esperava `"15"`, o que mascarava o preco errado. Corrigido para validar `"165"` e `"link"`. |
| 3 | CRITICO | JA OK | `src/zwaf/core/team.py` | `prompt_injection` ja retorna `agent_used="guard"` quando o InputGuard bloqueia. Sem alteracao necessaria. |
| 4 | ALTO | FIXED/PENDENTE | `tenants/livia-raiz-vital/prompts/vendedor.md` | Prompt do vendedor dizia `R$150` e colageno para New Woman. Corrigido para ingredientes e precos reais. Atendimento Alpha Pulse pela Livia foi bloqueado no prompt; ainda falta definir agente masculino. |
| 5 | CRITICO | FIXED/PENDENTE | `src/zwaf/tools/payment.py` | Payload Abacate Pay enviava apenas `externalId`/`quantity`, lia `price_cents` inexistente e dependia de placeholders. Corrigido para enviar `externalId`, `name`, `description`, `quantity`, `price`, `returnUrl` e `completionUrl`. Fernando ainda precisa preencher chave e URLs reais. |
| 6 | CRITICO | FIXED | `Dockerfile`, `zwaf/__init__.py` | `python -m harnesses...` e `uvicorn zwaf...` nao encontravam `src/zwaf`. Adicionado shim local e `PYTHONPATH=/app/src` no container. |
| 7 | CRITICO | FIXED | `src/zwaf/core/base_agent.py`, `requirements.txt` | Agno atual nao aceita `add_history_to_messages`/`storage`. Atualizado para `add_history_to_context`, `num_history_runs` e `AsyncPostgresDb`; adicionada dependencia `psycopg[binary]`. |
| 8 | ALTO | FIXED | `harnesses/setup_harness.py`, `docker-compose.client.yml` | Webhook Evolution API v2 esperava `enabled`, `webhookByEvents`, `webhookBase64`; harness usava chaves antigas. Corrigido. Env global atualizado para `WEBHOOK_GLOBAL_WEBHOOK_BY_EVENTS=false`. |
| 9 | MEDIO | PENDENTE | `infra/migrations/001_initial_schema.sql` | Migration cria `leads`, `zwaf_sessions`, `knowledge_base`, `payment_events` e `CREATE EXTENSION vector`. Campo `consent` nao existe em `leads`, apesar de estar citado no criterio de auditoria. Nao bloqueia harness atual, mas deve ser incluido em migracao futura se LGPD consent for persistido. |
| 10 | MEDIO | OK COM LIMITACAO | `src/zwaf/agents/fidelizacao.py`, `src/zwaf/api/main.py` | Scheduler inicia no `build_team()` durante lifespan e nao conecta ao DB no boot; a query so roda no job das 9h. Sem race critica de boot identificada. |
| 11 | MEDIO | OK PARA 1 TENANT | `docker-compose.client.yml`, `harnesses/setup_harness.py` | `WEBHOOK_GLOBAL_URL` usa um tenant fixo. Evolution API nao expande wildcard de tenant; para Livia single-tenant esta OK. Multi-tenant por virgula em `ZWAF_TENANTS` nao deve ser usado nessa URL. |

## Variaveis que Fernando precisa preencher

- `EVOLUTION_API_KEY`
- `WA_NUMBER_1=5511967318916`
- `WA_INSTANCE_1`
- `WA_WARMUP_START_DATE`
- `ABACATE_PAY_KEY`
- `ABACATE_PAY_WEBHOOK_SECRET`
- `ABACATE_PAY_RETURN_URL`
- `ABACATE_PAY_COMPLETION_URL`
- `OPENAI_API_KEY`
- `ZWAF_API_KEYS`
- `POSTGRES_PASSWORD`
- `CORS_ORIGINS`
- Opcional: `OPENROUTER_API_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`

## Sequencia de deploy

```bash
# EC2: 18.228.193.55 (sa-east-1)
# SSH: ssh -i sofia-sdr-prod.pem ubuntu@18.228.193.55

# 1. Preencher .env.livia-raiz-vital
#    EVOLUTION_API_KEY, WA_NUMBER_1, WA_INSTANCE_1,
#    WA_WARMUP_START_DATE, ABACATE_PAY_KEY,
#    ABACATE_PAY_WEBHOOK_SECRET, ABACATE_PAY_RETURN_URL,
#    ABACATE_PAY_COMPLETION_URL, OPENAI_API_KEY

# 2. Subir stack
docker compose -f docker-compose.client.yml \
  --env-file .env.livia-raiz-vital up -d

# 3. Validar stack (sem Fernando)
python -m harnesses.setup_harness --pre-qr

# 4. COM FERNANDO - scan QR
python -m harnesses.setup_harness --post-qr

# 5. Rodar suite completa
python -m harnesses.conversation_harness --all

# 6. Registrar webhook Abacate Pay:
#    URL: http://18.228.193.55:8000/v1/webhook/payment/livia-raiz-vital
```

## Veredito

Pronto para mock harness: **10/10 aprovado**.  
Pre-QR real ainda depende de stack rodando, credenciais Evolution/Abacate/OpenAI e scan do chip por Fernando.  
Antes do go-live, validar em ambiente real que Abacate Pay aceita os dados de retorno configurados e que `WA_INSTANCE_1` corresponde ao chip `+5511967318916`.
