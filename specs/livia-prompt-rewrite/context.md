# Context — Como o vendedor.md vive na arquitetura do ZWAF

> Fase 2 do Spec Driven Development. Aterramento técnico (anti-alucinação) lido do código real
> em `packages/zwaf/src/zwaf/`. Dependências que o novo prompt **não pode quebrar**.

---

## 1. Pipeline de uma mensagem (visão geral)

```
WhatsApp (Evolution) → api/routes/webhook.py → ZWAFTeam.process()
   → RouterAgent.route(message, phone)        # escolhe QUAL agente
   → build_<agente>_agent(...)                # constrói o Agno Agent do tenant
   → agent.arun(message)                      # LLM responde
   → send_response (typing sim + MESSAGE_SPLIT)
   → [pós-resposta] maybe_update_lead_memory() # story-044, async, throttled
```

O **vendedor** é o agente **default** do router (fallback quando nenhuma keyword/intent bate, e
para saudações sem histórico de compra).

## 2. Como o system prompt é montado — `core/base_agent.py`

`_load_prompt(tenant_id, "vendedor")`:
1. Lê `tenants/livia-raiz-vital/prompts/vendedor.md`.
2. Se existir `vendedor.kb.md` no mesmo dir, **anexa**: `vendedor.md + "\n\n---\n\n" + vendedor.kb.md`.
3. Em `build_agent`, se `lead_memory_block` (story-044) for não-vazio, **anexa de novo**:
   `... + "\n\n---\n\n" + lead_memory_block`.

**Ordem final no `instructions` do Agno Agent:**
```
[vendedor.md  — as 20 seções novas]
---
[vendedor.kb.md — KB de persuasão da story-036]
---
[bloco "## Memória deste lead" — só para lead recorrente, flag ON]
```

Config do Agent: `add_history_to_context=True`, `num_history_runs=10`, `reasoning=False`,
`markdown=False`. **Implicações para o prompt:**
- `markdown=False` → reforça a regra "sem markdown/bullets" do FORMATO DE COMUNICAÇÃO (seção 13);
  a saída deve ser conversacional, não formatada.
- O KB vem **depois** do prompt → o `vendedor.md` é a autoridade primária; o KB é reforço. Não
  duplicar o KB literalmente; referenciar princípios e deixar o KB detalhar.
- O bloco de memória vem **por último** → a seção 14 (MEMÓRIA DE CONTEXTO ATIVA) deve **instruir
  como usar** esse bloco, mantendo as regras anti-creepy/LGPD da story-044 (não recitar dossiê, dor
  como pergunta de cuidado, tudo corrigível).

## 3. Modelo e temperatura — `_make_llm`

`tenant_config.llm.primary` = `gpt-4o` (mantido — ADR Ramo 10), `temperature` lida do config.
Mudar para **0.7** afeta diretamente este caminho. GPT-4o é mais complacente em objeções
(pesquisa §4/§12) → o prompt precisa de **instinto de fechamento explícito** (seção 10) e
**âncoras de identidade** (constraint persistence) para compensar.

## 4. Como `objecoes.md` e `new-woman.md` chegam ao modelo — `tools/catalog.py`

**NÃO entram no system prompt.** O vendedor tem a tool `search_catalog` (closure por tenant):
- Faz keyword-scoring sobre `knowledge/*.md` (termos > 2 chars).
- Retorna o(s) arquivo(s) com mais hits **inteiro(s)** (até 2). Sem match → primeira seção de cada
  arquivo (até o 1º `---`).

Consequências para o design:
- O **tratamento de objeções primário** vive em `vendedor.md` (seções 11/12), sempre em contexto.
  `objecoes.md` é uma **referência recuperável** — vale como battle cards bem formatados que a tool
  devolve quando a cliente objeta.
- 🔴 `new-woman.md` é recuperável e hoje contém **preços stale** (185/165,90; "frete grátis acima de
  R$300"). Risco de o modelo citar preço errado. → corrigir (spec §4).
- `rag.py` (pgvector) é **stub Fase 2** (`NotImplementedError`) — não usar; catalog é o caminho vivo.

## 5. Roteamento — `core/router_agent.py` (NÃO confundir com a tabela de intent do prompt)

`RouterAgent` decide **qual agente** atende (vendedor/recompra/suporte/cobrança), por keyword
(substring case-insensitive, prioridade Cobrança>Suporte>Recompra>Vendedor) e LLM fallback;
default = vendedor. Saudação curta sem histórico → vendedor; com histórico → recompra.

A **CLASSIFICAÇÃO DE INTENT (seção 7 do prompt)** é outra coisa: é o raciocínio **interno do
vendedor** sobre o **momento da cliente** (symptom_inquiry, pricing_inquiry, purchase_intent,
objection_handling, …) para escolher a ação dentro da conversa. As duas camadas coexistem:
o router traz a cliente ao vendedor; a tabela de intent guia o que o vendedor faz a seguir.

Keywords do vendedor que o router já reconhece (config.json `router.keywords.vendedor`):
"quanto custa", "como funciona", "preço", "pix", "quero pagar", "fechar pedido"… — o prompt deve
estar pronto para esses momentos (pricing_inquiry, purchase_intent).

## 6. Checkout (story-035) — dependência crítica a preservar

A tool `make_guarded_payment_link_generator` (`conversion/payment_gate.py`) + `payment_result_sink`
fazem o checkout **determinístico**: o sistema coleta CPF/CEP (formulário + ViaCEP), gera Pix
copia-e-cola / link de cartão, e mensagens críticas (ex.: CPF inválido) são enviadas **literalmente**
sem paráfrase do LLM. O prompt (seções 15/16) deve **só conduzir até a decisão e confirmar
quantidade** — nunca pedir PII, nunca inventar Pix/URL, nunca dizer "enviei o link" sem o sistema ter
enviado. (Regras já presentes no vendedor.md atual; preservar integralmente.)

## 7. Memória de lead (story-044) — dependência crítica a preservar

Bloco `## Memória deste lead` é montado por `memory/lead_memory.py` (Camada 1 determinística +
Camada 2 summarizer Gemini) e injetado por request só para lead recorrente (flag
`lead_memory.enabled=true`, ON em prod). Sintoma é **dado de saúde cifrado (Fernet)**, purgado no
opt-out. A seção 14 do novo prompt deve manter as regras: usar como vendedor que lembra (não
sistema que vigia), dor como pergunta de cuidado, tudo como pergunta corrigível, nunca recitar o
bloco nem revelar que há "perfil".

## 8. Harness — `harnesses/conversation_harness.py`

10 cenários. Roda com `team=None` → **mock** (`_get_mock_responses`), sem LLM real: testa roteamento
+ asserts de string (`expected_contains`/`forbidden_contains`/latência). **Não** valida o prompt real.
Para a story-045: atualizar mocks + asserts como **contrato comportamental** (golden) e adicionar
cenários das seções novas. Execução (`--all`, `ruff`, `mypy`) no container/VPS (Python indisponível
no path do Drive).

## 9. Resumo das invariantes (o novo prompt NÃO pode quebrar)
1. Não pedir CPF/CEP/endereço; não inventar Pix/URL (035).
2. Não dizer "enviei o link" sem o sistema enviar (035).
3. Memória de lead anti-creepy/LGPD (044).
4. Só New Woman; Alpha Pulse → Caio.
5. Sem claim de cura/garantia; escalar Fernando em risco de saúde.
6. Preços só 149/128/119,90; sem desconto fora de faixa; sem escassez falsa.
7. Opt-out respeitado.
8. Saída conversacional (markdown=False).
