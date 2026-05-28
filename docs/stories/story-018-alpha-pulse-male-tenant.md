# Story 018 - Tenant masculino Alpha Pulse

**Status:** Ready for Review
**Sprint:** Sprint 18
**Epic:** ZWAF - Operacao Raiz Vital multi-persona
**Criado:** 2026-05-28
**Validado:** 2026-05-28 (Axis - product-lead, story local Ready)
**Track:** Brownfield Medium Risk

---

## Contexto

A Raiz Vital precisa separar o atendimento por produto e persona:

- `livia-raiz-vital` vende apenas New Woman.
- Um novo tenant masculino atende Alpha Pulse e vende apenas Alpha Pulse.

O sistema ZWAF ja descobre tenants por pasta em `tenants/` e isola prompts, knowledge base,
configuracao de WhatsApp, roteamento, pagamentos e sessoes por `tenant_id`.

## Escopo

### IN
- Criar tenant `caio-alpha-pulse` como copia funcional da Livia, com persona masculina.
- Manter apenas knowledge base de Alpha Pulse no tenant masculino.
- Restringir produtos de pagamento do tenant masculino aos SKUs `alpha-pulse-*`.
- Restringir produtos de pagamento da Livia aos SKUs `new-woman-*`.
- Atualizar prompts para impedir cross-sell: Livia nao vende Alpha Pulse; Caio nao vende New Woman.
- Documentar envs de WhatsApp separadas para Livia e Caio em `.env.example`.
- Adicionar teste/harness que valida isolamento de tenant e catalogo.

### OUT
- Criar nova infraestrutura Evolution API.
- Criar conta Abacate Pay ou product IDs reais.
- Alterar schema de banco.
- Alterar Caio gestor de trafego em `packages/caio-trafego/`.

## Acceptance Criteria

```gherkin
DADO que `ZWAF_TENANTS=livia-raiz-vital,caio-alpha-pulse`
QUANDO a API inicializa
ENTAO os dois tenants carregam sem erro

DADO o tenant `livia-raiz-vital`
QUANDO consulto sua configuracao de pagamento e catalogo
ENTAO apenas SKUs New Woman estao disponiveis
  E a knowledge base nao inclui Alpha Pulse

DADO o tenant `caio-alpha-pulse`
QUANDO consulto sua configuracao de pagamento e catalogo
ENTAO apenas SKUs Alpha Pulse estao disponiveis
  E a knowledge base nao inclui New Woman

DADO que um lead pede New Woman ao Caio
QUANDO o vendedor responde
ENTAO ele nao vende New Woman e orienta atendimento pela Livia

DADO que um lead pede Alpha Pulse a Livia
QUANDO a vendedora responde
ENTAO ela nao vende Alpha Pulse e orienta atendimento pelo Caio
```

## Dev Technical Guidance

### Existing System Context
- Tenant configs: `tenants/{tenant_id}/config.json`
- Prompts: `tenants/{tenant_id}/prompts/*.md`
- Catalogo: `tenants/{tenant_id}/knowledge/*.md`
- Tenant discovery: `src/zwaf/api/main.py::_discover_tenants`
- Config loader: `src/zwaf/core/tenant.py::TenantConfig.load`
- Payment factory: `src/zwaf/tools/payment.py::make_payment_link_generator`
- Catalog factory: `src/zwaf/tools/catalog.py::make_catalog_search`

### Integration Approach
- Usar isolamento existente por pasta de tenant.
- Nao criar novos imports ou dependencias.
- Nao tocar na arquitetura multi-agent.
- Validar por teste unitario com env vars mockadas.

## Tasks / Subtasks

- [x] Criar story e validar escopo brownfield.
- [x] Criar tenant `caio-alpha-pulse`.
- [x] Restringir Livia a New Woman.
- [x] Restringir Caio a Alpha Pulse.
- [x] Atualizar `.env.example` com envs dos dois chips/instances.
- [x] Adicionar teste de isolamento de tenant/produto.
- [x] Rodar harnesses e testes unitarios relevantes.

## Risk Assessment

### Implementation Risks
- **Primary Risk:** Cross-sell acidental por prompt ou catalogo compartilhado.
- **Mitigation:** Isolar knowledge e payment products por tenant.
- **Verification:** Teste unitario valida produtos e arquivos de knowledge por tenant.

### Rollback Plan
- Remover pasta `tenants/caio-alpha-pulse`.
- Reverter alteracoes em `tenants/livia-raiz-vital/config.json`, prompts e `.env.example`.

## CodeRabbit Integration

Story Type Analysis:
- Primary Type: Integration
- Complexity: Medium
- Risk Level: MEDIUM RISK
- Integration Points: tenant loader, catalog tool, payment config, prompts, env template

Quality Gate Tasks:
- [x] Pre-Commit (@developer): revisar diff e rodar testes locais.
- [ ] Pre-PR (@devops): executar quality gate antes de push/PR.

CodeRabbit Focus Areas:
- Regression prevention: Livia continua respondendo cenarios atuais.
- Integration safety: novo tenant carrega sem quebrar descoberta existente.
- Security: nenhum secret hardcoded.

## Definition of Done

- [x] `python -m harnesses.conversation_harness --all` passa.
- [x] `pytest tests/unit/ -q` passa.
- [x] Novo teste confirma isolamento Livia/New Woman e Caio/Alpha Pulse.
- [x] `.env.example` lista `ZWAF_TENANTS=livia-raiz-vital,caio-alpha-pulse`.
- [x] Nenhum secret real foi adicionado.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `pytest tests/unit/test_tenant_product_isolation.py -q` -> 4 passed
- `python -m harnesses.conversation_harness --all` -> 10/10
- `pytest tests/unit/ -q` -> 70 passed
- `python -m harnesses.report_harness` -> 4 harnesses passed
- Tenant smoke load for `livia-raiz-vital` and `caio-alpha-pulse` -> tenants OK

### Completion Notes

- Criado tenant `caio-alpha-pulse` com persona masculina Caio e catalogo Alpha Pulse.
- Livia agora tem apenas SKUs e knowledge de New Woman.
- Caio agora tem apenas SKUs e knowledge de Alpha Pulse.
- Prompts de venda, recompra, suporte, cobranca e fidelizacao explicitam o limite de produto por persona.
- `.env.example` documenta dois tenants e dois chips/instances.
- `docker-compose.client.yml` deixou de usar `ZWAF_TENANTS` em nomes de volumes e no webhook global para nao quebrar quando a variavel vira lista.

### File List

- `.env.example`
- `docker-compose.client.yml`
- `docs/stories/story-018-alpha-pulse-male-tenant.md`
- `tenants/livia-raiz-vital/config.json`
- `tenants/livia-raiz-vital/knowledge/alpha-pulse.md` (removido)
- `tenants/livia-raiz-vital/knowledge/objecoes.md`
- `tenants/livia-raiz-vital/prompts/recompra.md`
- `tenants/livia-raiz-vital/prompts/vendedor.md`
- `tenants/caio-alpha-pulse/config.json`
- `tenants/caio-alpha-pulse/knowledge/alpha-pulse.md`
- `tenants/caio-alpha-pulse/knowledge/objecoes.md`
- `tenants/caio-alpha-pulse/prompts/cobranca.md`
- `tenants/caio-alpha-pulse/prompts/fidelizacao.md`
- `tenants/caio-alpha-pulse/prompts/recompra.md`
- `tenants/caio-alpha-pulse/prompts/suporte.md`
- `tenants/caio-alpha-pulse/prompts/vendedor.md`
- `tests/unit/test_tenant_product_isolation.py`

### Change Log

| Data | Agente | Acao |
|------|--------|------|
| 2026-05-28 | Sync/Axis | Story criada e validada como Ready para desenvolvimento |
| 2026-05-28 | Pixel | Implementacao concluida e validada localmente |
