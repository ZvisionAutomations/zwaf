# Spec — Reescrita Estrutural do Prompt do Vendedor (Lívia / Raiz Vital)

> **Story:** `docs/stories/story-045-livia-vendedor-prompt-rewrite.md` (Ready, Track Heavy)
> **Fonte de decisão:** `ADR-livia-vendedor-prompt-rewrite.md` (2026-06-11) + `pesquisa-agente-vendas-2026.md`
> **Fase 1 do Spec Driven Development.** Rastreabilidade: cada instrução nova → ADR/KB/pesquisa (Artigo IV).

---

## 1. O que precisa ser feito (escopo exato)

1. **Reescrever `tenants/livia-raiz-vital/prompts/vendedor.md` do zero** em **20 seções nomeadas**
   (ordem fixa do ADR — ver `design.md`), transformando a identidade de *"consultora de
   bem-estar"* (informativa) em **vendedora consultiva que fecha** (empatia para diagnosticar,
   assertividade para fechar), com:
   - âncora de identidade no topo (constraint persistence — CAPITU);
   - Chain-of-Thought interno (7 passos RAIA);
   - tabela de classificação de intent adaptada para New Woman;
   - instinto de fechamento (A principal, B/C fallback);
   - guardrails negativos consolidados em **seção separada** (NUNCA/SEMPRE).
2. **Reestruturar `tenants/livia-raiz-vital/knowledge/objecoes.md`** no formato **battle card RAIA**
   (`objeção → princípio → resposta-modelo → próximo passo`), corrigindo preços para a fonte viva
   e removendo urgência falsa.
3. **`tenants/livia-raiz-vital/config.json`** → `llm.temperature: 0.4 → 0.7`.
4. **`harnesses/conversation_harness.py`** → atualizar cenários + mocks + asserts para refletir o
   comportamento novo e cobrir as seções críticas (abertura qualificadora, fechamento A, tom
   adaptativo, guardrails, preservação de checkout/memória).
5. **Correção de dado (ver §4, achado CRÍTICO):** alinhar os preços de `knowledge/new-woman.md`
   à fonte viva, porque a tool `search_catalog` injeta esse arquivo no modelo.

## 2. O que NÃO deve ser alterado

- **Lógica de código Python** (`base_agent.py`, `router_agent.py`, `team.py`, `tenant.py`,
  fluxo de checkout, `lead_memory.py`, `payment_gate.py`) — exceto o arquivo de teste `conversation_harness.py`.
- **Comportamento do checkout determinístico (story-035):** sistema coleta CPF/CEP via
  formulário+ViaCEP; a Lívia nunca pede PII nem inventa Pix/URL; confirma quantidade.
- **Memória de lead (story-044, flag ON em prod):** retomada por nome, Pix em aberto, dor como
  pergunta de cuidado, anti-creepy/LGPD. A seção 14 do novo prompt **integra**, não substitui.
- **Pricing tiered (story-028):** faixas 149 / 128 / 119,90; frete grátis; cartão +10%.
- **Roteamento (`router_agent.py`):** keyword→LLM→default vendedor. A "tabela de intent" do
  prompt é raciocínio INTERNO do vendedor sobre o momento da cliente — **não** o router.
- **Prompts dos outros agentes** (recompra/suporte/cobrança/fidelização) — próxima sessão.
- **Prova social real:** seção existe mas fica **desativada** até material do Fernando. Sem inventar.
- **Temperatura ≠ 0.7.**

## 3. Critérios de aceitação mensuráveis

Herdados dos ACs da story-045 (Given/When/Then). Resumo verificável:

| # | Critério | Como medir |
|---|----------|-----------|
| AC1 | 1ª mensagem = saudação + apresentação + pergunta qualificadora; sem produto/preço | revisão manual + cenário harness `abertura_qualificadora` |
| AC2 | Fechamento A é o padrão após interesse/objeção resolvida; B/C só fallback | seção 10 presente + cenário `fechamento_apos_objecao` |
| AC3 | Objeções via battle card RAIA; ≤2 tentativas; escala Fernando em risco de saúde | seções 11/12 + cenários `objecao_*` |
| AC4 | Tom adaptativo por estado (decidida/hesitante/frustrada/animada) | seção 17 + cenário `tom_adaptativo` |
| AC5 | Oferta ancorada 149→128→119,90 como "tratamento"; sem desconto fora de faixa; sem escassez falsa | seções 9 + 19 + `forbidden_contains` no harness |
| AC6 | Memória de lead (044) intacta | seção 14 preserva instruções; cenário `lead_recorrente` |
| AC7 | Checkout (035) intacto | seção 16 preserva regras; cenário `checkout_*` |
| AC8 | `conversation_harness --all` → 10/10; `ruff check` + `mypy` limpos | execução no container/VPS |
| AC9 | temperature 0.7; nenhum guardrail removido/enfraquecido | diff config + revisão da seção 19 |
| AC10 | Cada bloco novo rastreável (ADR/KB/pesquisa) | comentários/anotações no prompt |

## 4. Achados que afetam o escopo (descobertos no aterramento de arquitetura)

- **🔴 CRÍTICO — preços stale em `new-woman.md`.** "Política Comercial" lista R$185/165,90 (cartão/Pix),
  kits 2-3 potes diferentes e "frete grátis acima de R$300". A fonte viva (`config.json`/story-028)
  é 149/128/119,90 + frete grátis total. Como `search_catalog` (tool do vendedor) devolve o arquivo
  **inteiro** ao modelo, há risco direto de a Lívia citar preço errado → fura o guardrail de pricing
  (AC5). **Recomendação:** corrigir a "Política Comercial" de `new-woman.md` para a fonte viva nesta
  story (correção de consistência, rastreável). *Fora do IN original da missão — sinalizado para
  decisão do operador; default = corrigir.*
- **🔴 harness é mock.** `conversation_harness.py` roda com `team=None` → respostas hardcoded em
  `_get_mock_responses`; não exercita o LLM/prompt real. Os mocks atuais usam preço **165** (stale) e
  comportamento informativo antigo. Para o "10/10" ter sentido, os mocks + `expected/forbidden_contains`
  serão atualizados para codificar o **contrato comportamental novo** (golden responses), e cenários
  das seções novas serão adicionados. Validação comportamental real = teste E2E no WhatsApp pós-deploy.
- **Carregamento do prompt:** ordem `vendedor.md` + `---` + `vendedor.kb.md` + `---` + `lead_memory_block`.
  O novo `vendedor.md` deve ser coerente com o KB anexado depois (não duplicar/contradizer) e com o
  bloco de memória (seção 14 espelha o comportamento que o bloco 044 injeta).
- **Execução de testes:** Python indisponível no path do Google Drive (histórico stories 035/036/044);
  `harness --all`, `ruff`, `mypy` rodam no **container/VPS**. AC8 verificado no ambiente de deploy.

## 5. Arquivos criados/modificados

| Arquivo | Ação |
|---------|------|
| `specs/livia-prompt-rewrite/spec.md` | criar (este) |
| `specs/livia-prompt-rewrite/context.md` | criar |
| `specs/livia-prompt-rewrite/design.md` | criar |
| `specs/livia-prompt-rewrite/testes.md` | criar |
| `tenants/livia-raiz-vital/prompts/vendedor.md` | **reescrever do zero** (20 seções) |
| `tenants/livia-raiz-vital/knowledge/objecoes.md` | **reestruturar** (battle cards RAIA + preços + sem urgência falsa) |
| `tenants/livia-raiz-vital/knowledge/new-woman.md` | corrigir "Política Comercial" (achado CRÍTICO §4) |
| `tenants/livia-raiz-vital/config.json` | `temperature: 0.7` |
| `harnesses/conversation_harness.py` | atualizar cenários/mocks/asserts |
| `docs/stories/story-045-...md` | Dev Agent Record / QA / Change Log |

## 6. Riscos & mitigação
- Identidade "fecha" → pressão: mitiga seção 19 + persistência ≤2.
- 0.7 reverte aderência deliberada (06-10): mitiga estrutura/âncoras; revalidar pós-deploy.
- Regressão checkout/memória: ACs + cenários dedicados; preservação literal das seções 035/044.
- Claim médico na linguagem de fechamento: revisão contra guardrails (seção 19) + KB §6.
