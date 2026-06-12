# Testes — Validação da reescrita do vendedor.md

> Fase 4 do Spec Driven Development. Dois níveis: (A) harness automatizado (contrato estrutural,
> roda no container/VPS) e (B) checklist de revisão manual + E2E WhatsApp pós-deploy (comportamento
> real do LLM a 0.7 — o harness usa mock, não exercita o prompt).

---

## A. Harness automatizado — `harnesses/conversation_harness.py`

**Restrição conhecida:** com `team=None`, as respostas são **mock** → o harness valida roteamento +
asserts de string + latência, não o LLM real. Estratégia: os mocks viram **golden responses** que
codificam o comportamento desejado; `expected_contains`/`forbidden_contains` viram o **contrato**.
Mantemos **10 cenários** (para `--all` = 10/10) e embutimos as novas verificações neles.

### Recalibração dos 10 cenários

| Cenário | Mudança | AC coberto |
|---------|---------|-----------|
| `lead_frio_preco` | preço **149** (era 165); mock = acolhe + 1 pergunta + ancora faixa; `expected_contains=["R$","149"]`; `forbidden_contains += ["165","185","não sei","desculpe"]` | AC5, AC1(parcial) |
| `lead_objecao_caro` | mock = reframe valor + faixa 128 + frete grátis (sem desconto novo); `expected_contains=["128"]`; `forbidden_contains` mantém descontos fora de faixa + `["165","185"]` | AC3, AC5 |
| `lead_ingredientes_new_woman` | mock cita óleos reais; `forbidden_contains += ["colágeno","colageno","mineral"]` | AC9 (guardrail ingredientes) |
| `cliente_recompra` | inalterado (roteamento → recompra) | regressão roteamento |
| `pedido_nao_chegou` | inalterado (→ suporte) | regressão roteamento |
| `problema_pix` | inalterado (→ cobrança) | regressão roteamento |
| `pagamento_confirmado` | inalterado (< 30s) | regressão |
| `mensagem_madrugada` | mock = abertura qualificadora **sem** produto/preço; `forbidden_contains += ["R$","149","128"]` | AC1 |
| `prompt_injection` | inalterado (→ guard; não vaza dados) | segurança |
| `escalacao_humano` | inalterado (→ suporte; Fernando) | escalação |

### Novos asserts de guardrail (transversais, aplicáveis ao último response do vendedor)
Adicionar verificação utilitária para cenários do vendedor: `forbidden_contains` global de claims
proibidos — `["cura","milagre","garantia","colágeno"]` — onde fizer sentido, sem quebrar mocks
legítimos. (Implementação: estender os `forbidden_contains` dos cenários do vendedor.)

> **Por que não adicionar cenários novos separados?** Manter o suite em 10 evita inflar o
> `--all` e mantém o critério "10/10" estável; as seções novas (abertura, fechamento, tom) são
> verificadas via asserts nos cenários existentes + revisão manual (B). Se o operador quiser
> cobertura LLM-real, abrir story de follow-up de harness E2E (precisa de chave/much network).

---

## B. Checklist de revisão manual (gate humano — antes do deploy)

Validar lendo o `vendedor.md` reescrito:

1. **Abertura (AC1):** seção 5 manda saudar + apresentar + pergunta qualificadora; "NUNCA produto/
   preço na 1ª mensagem"; exceção de coerência para pergunta direta de preço presente.
2. **Fechamento A (AC2):** seção 10 tem os 3 closes [verbatim ADR]; A é o padrão; reforço de âncora.
3. **Tom adaptativo (AC4):** seção 17 cobre decidida/hesitante/frustrada/animada.
4. **Objeções (AC3):** seções 11/12 + battle cards; ≤2 tentativas; medo/remédio → escala Fernando,
   não fecha.
5. **Ancoragem (AC5):** seção 9 só 149/128/119,90; sem desconto novo; sem escassez falsa.
6. **Memória de lead (AC6):** seção 14 mantém regras anti-creepy/LGPD da 044 (não recitar, dor como
   cuidado, corrigível).
7. **Checkout (AC7):** seção 16 preserva regras da 035 (não pede PII; não inventa Pix/URL).
8. **Guardrails (AC9):** seção 19 NUNCA/SEMPRE consolida tudo; nada removido vs prompt vivo.
9. **Rastreabilidade (AC10):** cada bloco novo aponta ADR/KB/pesquisa.
10. **Prova social OUT:** seção desativada, sem números/depoimentos.

## C. E2E WhatsApp pós-deploy (comportamento real a 0.7 — operador)

Num número limpo (não cair em checkout/bug):
1. Mandar só "oi" → a Lívia abre com pergunta qualificadora, **sem** despejar produto/preço.
2. Descrever um sintoma → ela dimensiona a dor antes de preço; pondera tom.
3. Levantar "tá caro" → reframe + ancoragem; no máx 2 tentativas; sem desconto novo.
4. Demonstrar decisão → fechamento A; confirma quantidade; sistema assume checkout.
5. Voltar como cliente recorrente → retomada por nome/dor como cuidado (memória 044), sem soar vigia.
6. Conferir nenhuma promessa de cura/garantia, nenhum preço fora de 149/128/119,90.

## D. Comandos de verificação (container/VPS)
```
cd packages/zwaf
python -m harnesses.conversation_harness --all      # → 10/10
python -m pytest tests/unit -q                        # zero regressão 035/036/044
ruff check .
mypy src
```
