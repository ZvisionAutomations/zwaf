# Story 046 - Livia Fase 4: Reescrita dos prompts de cobranca, fidelizacao, recompra e suporte

**Status:** InReview
**Sprint:** Sprint 46
**Epic:** ZWAF - Go-Live Controlado WhatsApp
**Criado:** 2026-06-11
**Validado:** 2026-06-11 (@product-lead / Axis - validacao local)
**Track:** Heavy
**Prioridade:** P1
**Autor:** @sprint-lead (Sync)
**Insumo principal:** `ADR-livia-todos-agentes-prompt-rewrite.md` (Mega ADR, 2026-06-11)
**Insumos de apoio:** `ADR-livia-vendedor-prompt-rewrite.md`, `pesquisa-agente-vendas-2026.md`,
`tenants/livia-raiz-vital/prompts/vendedor.md`, `tenants/livia-raiz-vital/knowledge/new-woman.md`
**Metodologia:** Spec Driven Development (spec -> context -> design -> testes -> implementacao -> review)

---

## Contexto

A story-045 reescreveu `vendedor.md` como modelo canonico de estrutura, identidade e guardrails da
Livia. Restam quatro agentes vivos com identidade mais fraca e estrutura antiga:

- `cobranca.md`
- `fidelizacao.md`
- `recompra.md`
- `suporte.md`

Esses agentes precisam manter a cliente sentindo que fala com a mesma Livia, mesmo quando o router
troca o agente por contexto operacional. A missao e aplicar um bloco base identico de identidade e
reescrever cada prompt com fluxos especificos, sem regredir checkout, memoria de lead, guardrails
medicos/comerciais ou a story-045.

## Problema

Os prompts atuais dos agentes restantes ainda usam a identidade antiga de "consultora de bem-estar"
e instrucoes curtas. Isso cria risco de:

- quebra de continuidade quando a conversa troca de agente;
- cobranca voltar a capturar Pix de checkout novo, regressao direta da story-035;
- suporte tentar vender em reclamacao, reacao adversa ou pedido de humano;
- recompra/fidelizacao usarem memoria de forma invasiva;
- agentes contradizerem os guardrails consolidados no `vendedor.md` da story-045.

## Escopo

### IN

- Criar specs em `specs/livia-outros-agentes/`:
  - `spec.md`
  - `context.md`
  - `design.md`
  - `testes.md`
- Reescrever `tenants/livia-raiz-vital/prompts/cobranca.md`:
  - recuperacao de venda;
  - cliente ja decidiu comprar;
  - battle cards em XML para Pix expirado, link com erro e boleto nao chegou;
  - tom direto, simpatico, sem upsell.
- Reescrever `tenants/livia-raiz-vital/prompts/fidelizacao.md`:
  - acionamento somente por evento operacional;
  - marcos `received_usage`, `delivery_15d`, `delivery_30d_coupon`;
  - fluxos por melhora, sem resultado, uso incorreto, negativa e sem resposta;
  - cupom de 10% somente como carta na manga no marco de 30 dias.
- Reescrever `tenants/livia-raiz-vital/prompts/recompra.md`:
  - fluxo com memoria positiva;
  - fluxo sem memoria;
  - link em no maximo 3 turnos;
  - upsell uma vez, apos confirmar dados, sem insistir.
- Reescrever `tenants/livia-raiz-vital/prompts/suporte.md`:
  - linha clara entre o que Livia resolve e o que vai para Fernando imediato;
  - tom adaptativo por duvida simples, problema operacional e problema critico;
  - encerramento de suporte sem venda forcada.
- Atualizar `harnesses/conversation_harness.py` com cenarios novos para os quatro agentes.

### OUT

- Alterar `tenants/livia-raiz-vital/prompts/vendedor.md`.
- Alterar `tenants/livia-raiz-vital/config.json`; `temperature` ja esta em 0.7.
- Alterar qualquer logica Python de producao.
- Alterar router, checkout, payment gate, lead memory, tenant loader ou integracoes.
- Alterar pricing, fluxo Asaas, Super Frete/Melhor Envio ou schema de banco.
- Adicionar prova social inventada ou ativar depoimentos sem material validado.
- Fazer push, PR ou deploy.

## Acceptance Criteria

```gherkin
DADO os quatro prompts restantes da Livia
QUANDO a reescrita for concluida
ENTAO `cobranca.md`, `fidelizacao.md`, `recompra.md` e `suporte.md` contem o mesmo bloco base de identidade
  E o bloco e identico entre os quatro arquivos

DADO uma cliente com Pix expirado
QUANDO o agente de cobranca assumir a conversa
ENTAO a Livia reconhece o pagamento em aberto e conduz para novo link em ate 2 turnos
  E nao faz diagnostico, upsell ou nova venda consultiva

DADO uma cliente sem resposta em fluxo de fidelizacao
QUANDO atingir 3 tentativas sem retorno
ENTAO a Livia encerra o fluxo ativo com dignidade
  E nao continua follow-up nem pressiona recompra

DADO uma cliente com memoria positiva de experiencia
QUANDO ela voltar para recomprar New Woman
ENTAO a Livia reconhece o retorno, confirma dados, oferece upsell uma vez se apropriado
  E chega ao link em no maximo 3 turnos

DADO uma cliente relatando problema critico no suporte
QUANDO houver reacao adversa, alergia, mal-estar, reembolso, devolucao, defeito, dano ou ameaca de Procon
ENTAO a Livia para o fluxo e aciona Fernando imediatamente
  E nao tenta resolver sozinha nem vender

DADO qualquer um dos quatro agentes
QUANDO responder sobre saude, preco, prova social ou checkout
ENTAO nenhum guardrail medico/comercial e removido ou enfraquecido
  E nenhuma prova social inventada e adicionada
  E o checkout deterministico e a memoria de lead permanecem preservados

DADO a entrega da implementacao
QUANDO os testes rodarem
ENTAO `python -m harnesses.conversation_harness --all` passa 10/10
  E `ruff check` e `mypy` ficam limpos na fase de review
```

## Dev Technical Guidance

- Package vivo: `packages/zwaf/`.
- Prompts por agente: `tenants/livia-raiz-vital/prompts/{agent}.md`.
- Ficha tecnica e pricing: `tenants/livia-raiz-vital/knowledge/new-woman.md`.
- Harness: `harnesses/conversation_harness.py`.
- Modelo canonico: `tenants/livia-raiz-vital/prompts/vendedor.md` da story-045.
- Memoria de lead: respeitar bloco `## Memoria deste lead` e comportamento anti-creepy da story-044.
- Checkout: preservar determinismo das stories 035/041; prompts nao devem pedir CPF/CEP/endereco nem
  inventar Pix/URL.

## Tasks / Subtasks

- [x] @sprint-lead: criar story da missao e promover para Ready apos validacao local.
- [x] @product-lead: validar escopo, ACs, riscos e valor de negocio.
- [x] @architect: criar `spec.md`, `context.md`, `design.md`, `testes.md` e validar arquitetura.
- [x] @developer: reescrever os quatro prompts e atualizar harness somente apos specs aprovadas.
- [x] @quality-gate: revisar bloco base identico, guardrails, transicoes, harness, `ruff` e `mypy`.
- [ ] @devops: push/PR/deploy somente se operador pedir.

## Risk Assessment

| Risco | Impacto | Mitigacao |
|-------|---------|-----------|
| Identidade divergente entre agentes | Cliente percebe troca de pessoa | Bloco base identico e revisao literal |
| Cobranca virar venda nova | Aumenta atrito no pagamento | Prompt direto, sem diagnostico/upsell |
| Fidelizacao parecer spam | Opt-out e desgaste da marca | Eventos operacionais e limite de 3 tentativas |
| Recompra insistir no upsell | Perda de confianca | Upsell uma vez e sem insistencia |
| Suporte vender em crise | Risco reputacional/compliance | Problema critico -> Fernando imediato |
| Prova social inventada | Quebra de confianca/compliance | Secao permanece desativada ate material real |
| Regressao checkout/memoria | Quebra de features em producao | ACs dedicados + harness + review manual |

## Definition of Done

- [x] `specs/livia-outros-agentes/spec.md` aprovado.
- [x] `specs/livia-outros-agentes/context.md` aprovado.
- [x] `specs/livia-outros-agentes/design.md` aprovado.
- [x] `specs/livia-outros-agentes/testes.md` aprovado.
- [x] Bloco base identico em `cobranca.md`, `fidelizacao.md`, `recompra.md`, `suporte.md`.
- [x] `cobranca.md` com battle cards XML e tom direto.
- [x] `fidelizacao.md` com 3 marcos, fluxos por estado e limite de 3 tentativas.
- [x] `recompra.md` com fluxos com/sem memoria, upsell uma vez e maximo 3 turnos.
- [x] `suporte.md` com linha de atuacao clara e tom adaptativo.
- [x] `conversation_harness.py` atualizado com novos cenarios.
- [x] `python -m harnesses.conversation_harness --all` passa 10/10.
- [x] `ruff check` e `mypy` limpos ou pendencia documentada se ambiente local impedir.
- [x] Nenhum guardrail medico/comercial removido.
- [x] Nenhum secret/PII real versionado.

## File List esperado

- `docs/stories/story-046-livia-outros-agentes-prompt-rewrite.md`
- `specs/livia-outros-agentes/spec.md`
- `specs/livia-outros-agentes/context.md`
- `specs/livia-outros-agentes/design.md`
- `specs/livia-outros-agentes/testes.md`
- `tenants/livia-raiz-vital/prompts/cobranca.md`
- `tenants/livia-raiz-vital/prompts/fidelizacao.md`
- `tenants/livia-raiz-vital/prompts/recompra.md`
- `tenants/livia-raiz-vital/prompts/suporte.md`
- `harnesses/conversation_harness.py`

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m harnesses.conversation_harness --all` -> 10/10.
- Bloco base identico nos 4 prompts -> `IDENTITY_BLOCK_IDENTICAL=True`.
- `.venv\Scripts\python.exe -m ruff check harnesses\conversation_harness.py` com cache em `%TEMP%` -> pass.
- `.venv\Scripts\python.exe -m mypy --cache-dir %TEMP%\zwaf-mypy-story046 --no-sqlite-cache harnesses\conversation_harness.py` -> pass.
- `.venv\Scripts\python.exe -m pytest tests\unit\test_router_agent.py tests\unit\test_evolution_webhook.py tests\unit\test_payment_gate.py tests\unit\test_lead_memory.py tests\unit\test_lead_memory_wiring.py -q` -> 64 passed.

### Completion Notes

- `cobranca.md` reescrito com identidade base, RAIA, fluxo direto de recuperacao de pagamento, memoria anti-creepy, battle cards XML e guardrails.
- `fidelizacao.md` reescrito com 3 marcos (`received_usage`, `delivery_15d`, `delivery_30d_coupon`), limite de 3 tentativas, cupom como carta na manga e escalacao em problema critico.
- `recompra.md` reescrito com fluxo com memoria positiva, fluxo sem memoria, upsell uma vez e meta de link em ate 3 turnos.
- `suporte.md` reescrito com linha Livia resolve vs Fernando imediato, tom adaptativo e encerramento sem venda forcada.
- `conversation_harness.py` atualizado para os 10 cenarios da story-046, mantendo `--all` em 10/10.
- Nenhuma logica Python de producao, `vendedor.md` ou `config.json` foi alterado.

### File List

- `docs/stories/story-046-livia-outros-agentes-prompt-rewrite.md`
- `specs/livia-outros-agentes/spec.md`
- `specs/livia-outros-agentes/context.md`
- `specs/livia-outros-agentes/design.md`
- `specs/livia-outros-agentes/testes.md`
- `tenants/livia-raiz-vital/prompts/cobranca.md`
- `tenants/livia-raiz-vital/prompts/fidelizacao.md`
- `tenants/livia-raiz-vital/prompts/recompra.md`
- `tenants/livia-raiz-vital/prompts/suporte.md`
- `harnesses/conversation_harness.py`

## Product Lead Validation

**Validador:** @product-lead (Axis)
**Data:** 2026-06-11
**Veredito:** GO / Ready

Checklist:

- Problema claro: prompts restantes divergem do padrao canonico da story-045.
- Valor de negocio claro: recupera venda, aumenta recompra e protege relacionamento/pos-venda.
- Escopo IN/OUT explicito e testavel.
- ACs cobrem os quatro agentes e os guardrails transversais.
- Dependencias de checkout, memoria, pricing e prova social mapeadas.
- Riscos principais documentados com mitigacao.
- Implementacao bloqueada ate specs aprovadas.

## QA Results

**Revisor:** @quality-gate (Litmus)  
**Data:** 2026-06-11  
**Veredito:** CONCERNS

### Checks

1. **Bloco base identico:** PASS - `IDENTITY_BLOCK_IDENTICAL=True` nos quatro prompts.
2. **Cobranca:** PASS - `payment_recovery_cards` presente com `pix_expirado`, `link_com_erro` e
   `boleto_ou_link_nao_chegou`; guardrails de CPF/CEP/endereco, Pix/URL inventado e escalacao apos 2
   tentativas presentes.
3. **Fidelizacao:** PASS - marcos `received_usage`, `delivery_15d`, `delivery_30d_coupon`; limite de
   3 tentativas; cupom bloqueado fora do marco de 30 dias.
4. **Recompra:** PASS - fluxos com/sem memoria, upsell uma vez e meta de pagamento em ate 3 turnos.
5. **Suporte:** PASS - linha Livia resolve vs Fernando imediato; problema critico escala Fernando;
   sem venda durante reclamacao/problema.
6. **Guardrails:** PASS - sem prova social inventada; guardrails medicos/comerciais preservados;
   checkout e memoria protegidos.
7. **Harness:** PASS - `.venv\Scripts\python.exe -m harnesses.conversation_harness --all` -> 10/10.
   Achado inicial corrigido: o harness agora falha quando `expected_agent` ou `max_turns` nao batem.
8. **Regressao unit completa:** PASS - `.venv\Scripts\python.exe -m pytest tests\unit -q --basetemp C:\Temp\zwaf-pytest-story046 -p no:cacheprovider` -> 335 passed, 1 warning.
9. **Lint/type no escopo alterado:** PASS - `ruff` e `mypy` passam em `harnesses\conversation_harness.py`.
10. **Lint/type repo-wide:** CONCERNS - `ruff check . --no-cache` e `mypy src harnesses` encontram
    pendencias pre-existentes fora dos arquivos alterados pela story-046.

### Residual Risk

- O harness e mock; E2E WhatsApp real deve ser feito antes de deploy para validar comportamento do LLM
  a 0.7 em conversa viva.
- Repo-wide `ruff` ainda aponta E741 em `src\zwaf\conversion\checkout_flow.py`, F541 em
  `tests\unit\test_payment_tool.py`, F401 em `tests\unit\test_team_checkout.py` e F401 em
  `tests\unit\test_viacep.py`.
- Repo-wide `mypy` ainda aponta pendencias em `src\zwaf\reporting\commercial_report.py`,
  `src\zwaf\memory\session.py` e `src\zwaf\memory\lead_memory.py`.
- O gate formal esta em `docs/qa/gates/story-046-livia-outros-agentes-prompt-rewrite-gate.md`.

## Change Log

| Data | Agente | Acao |
|------|--------|------|
| 2026-06-11 | @sprint-lead / Sync | Story criada a partir do Mega ADR e da missao do operador |
| 2026-06-11 | @product-lead / Axis | Story validada como Ready para fluxo SDD |
| 2026-06-11 | @architect / Stratum | Specs SDD criadas: spec, context, design e testes |
| 2026-06-11 | @developer / Pixel | Quatro prompts reescritos, harness atualizado e verificacoes locais executadas |
| 2026-06-11 | @quality-gate / Litmus | Gate CONCERNS; story scope PASS; harness 10/10; unit suite 335 passed; repo-wide ruff/mypy com pendencias pre-existentes |
