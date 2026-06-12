# Spec - Reescrita dos prompts restantes da Livia (Raiz Vital)

> Fase 1 do Spec Driven Development.
> Story: `docs/stories/story-046-livia-outros-agentes-prompt-rewrite.md` (Ready).
> Escopo: `cobranca.md`, `fidelizacao.md`, `recompra.md`, `suporte.md`.
> Referencias lidas: `vendedor.md` reescrito na story-045, prompts atuais dos 4 agentes,
> `new-woman.md`, Mega ADR de todos os agentes, ADR do vendedor, pesquisa 2026 e story-045.

---

## 1. Estado e gates

- A implementacao dos prompts fica BLOQUEADA ate aprovacao sequencial de:
  1. `spec.md`
  2. `context.md`
  3. `design.md`
  4. `testes.md`
- Pelo framework SINAPSE, codigo/implementacao exige story em `docs/stories/` com status >= `Ready`.
  Gate atendido por `docs/stories/story-046-livia-outros-agentes-prompt-rewrite.md`.
- Esta fase cria somente documentacao de especificacao. Nao altera prompts, `config.json`,
  checkout, memoria, router, harness ou codigo Python.

## 2. Escopo exato de arquivos

### Criar

| Arquivo | Fase | Objetivo |
|---------|------|----------|
| `specs/livia-outros-agentes/spec.md` | 1 | Escopo, nao-escopo, criterios de aceite e dependencias |
| `specs/livia-outros-agentes/context.md` | 2 | Arquitetura ZWAF, router, memoria e relacao entre agentes |
| `specs/livia-outros-agentes/design.md` | 3 | Estrutura completa dos prompts e fluxos |
| `specs/livia-outros-agentes/testes.md` | 4 | Cenarios de teste e plano de harness |

### Modificar somente na Fase 5

| Arquivo | Acao permitida | Observacao |
|---------|----------------|------------|
| `tenants/livia-raiz-vital/prompts/cobranca.md` | Reescrever | Recuperacao de venda, battle cards XML, tom direto |
| `tenants/livia-raiz-vital/prompts/fidelizacao.md` | Reescrever | Pos-venda por evento operacional, 3 marcos e opt-out |
| `tenants/livia-raiz-vital/prompts/recompra.md` | Reescrever | Recompra rapida, com/sem memoria, upsell uma vez |
| `tenants/livia-raiz-vital/prompts/suporte.md` | Reescrever | Resolver problemas, duvidas e escalacao clara |
| `harnesses/conversation_harness.py` | Atualizar cenarios/mocks/asserts | Apenas harness de teste, sem alterar logica de producao |

### Reutilizar como fonte, sem alterar

| Arquivo | Uso |
|---------|-----|
| `tenants/livia-raiz-vital/prompts/vendedor.md` | Modelo canonico da story-045 e fonte de guardrails |
| `tenants/livia-raiz-vital/knowledge/new-woman.md` | Ficha tecnica, pricing e restricoes reais |
| `tenants/livia-raiz-vital/config.json` | Fonte viva de configuracao; nao alterar temperature |
| `docs/stories/story-045-livia-vendedor-prompt-rewrite.md` | Historico, decisoes e DoD do vendedor |
| `C:\Users\Suporte\Downloads\ADR-livia-todos-agentes-prompt-rewrite (1).md` | Briefing principal desta missao |
| `C:\Users\Suporte\Downloads\ADR-livia-vendedor-prompt-rewrite.md` | Decisoes de estrutura e guardrails |
| `C:\Users\Suporte\Downloads\pesquisa-agente-vendas-2026.md` | Embasamento de pesquisa |

## 3. O que NAO deve ser alterado

- Nao alterar nenhuma logica Python de producao.
- Nao alterar `src/zwaf/api/routes/webhook.py`, router, team, base agent, payment gate,
  lead memory, ferramentas de checkout ou integracoes Asaas/Super Frete/Melhor Envio.
- Nao alterar o fluxo de checkout das stories 035/041.
- Nao alterar `tenants/livia-raiz-vital/prompts/vendedor.md`.
- Nao alterar `tenants/livia-raiz-vital/config.json`; `temperature` permanece 0.7.
- Nao alterar pricing aprovado: Pix R$149, R$128 de 2 a 4 potes, R$119,90 a partir de 5 potes;
  cartao cerca de 10% a mais; frete gratis no momento.
- Nao adicionar prova social inventada, numero de clientes, depoimentos, fotos ou prints nao
  validados.
- Nao remover ou enfraquecer guardrails medicos, comerciais, opt-out, LGPD/anti-creepy e
  escalacao para Fernando.
- Nao mexer em dados sensiveis, secrets, `.env`, migrations ou infraestrutura.
- Nao fazer push, PR ou deploy; isso e exclusivo de @devops.

## 4. Bloco base obrigatorio

Os quatro prompts devem conter um bloco base identico, conforme briefing do operador/Mega ADR:

- Identidade: Livia, especialista em bem-estar feminino da Raiz Vital.
- Apresentacao padrao quando necessario.
- Resposta padrao para "voce e robo?".
- Voz: amiga especialista, calorosa, energetica, confiante quando precisa.
- Emojis moderados.
- Tratamento natural alternando nome e "voce".
- Mensagens com maximo de 3-4 linhas e uma ideia por vez.
- Transicao entre agentes sem reset em conversa ativa; referencia leve apos pausa longa.
- Memoria de contexto: usa o que a cliente ja disse, sem repetir perguntas.
- Cliente agressiva: cordial/firme, encerra spam/xingamento sem contexto, escala Fernando se preciso.
- Cliente emocional: para o fluxo, acolhe, retorna apenas quando fizer sentido.

Na Fase 5, o bloco deve ser copiado de forma byte-for-byte identica nos 4 prompts ou extraido para
constante reutilizavel se existir mecanismo real no ZWAF para isso. Se nao houver mecanismo de prompt
partials, a alternativa segura e duplicar o bloco identico nos arquivos.

## 5. Criterios de aceitacao por agente

### 5.1 `cobranca.md`

- Dado uma cliente com Pix expirado, quando ela pedir ajuda, entao Livia deve reconhecer o pedido em
  aberto, gerar/encaminhar novo link sem diagnostico de sintomas e resolver em ate 2 turnos.
- Deve conter objetivo claro: recuperacao de venda; cliente ja decidiu comprar; remover atrito.
- Deve conter RAIA interno adaptado para cobranca: entender problema, aplicar guardrails, agir
  imediatamente, confirmar resolucao.
- Deve conter fluxo principal: identificar problema -> agir imediatamente -> orientar uso do Pix se
  necessario -> confirmar resolucao.
- Deve preservar memoria de lead para quantidade, valor, forma de pagamento e link/Pix em aberto,
  sem revelar perfil/anotacoes e sem usar dor de saude como pressao.
- Deve conter battle cards em XML para pelo menos:
  - Pix expirado
  - Link com erro
  - Boleto nao chegou
- Deve escalar Fernando apos 2 tentativas sem resolver e imediatamente em reclamacao critica,
  reacao adversa, reembolso/devolucao, ameaca de Procon ou insistencia por humano.
- Deve proibir dados bancarios diretos, Pix/URL inventados e afirmacao de link enviado sem URL real.
- Nao deve fazer upsell, diagnostico, SPIN/DDPOF ou nova venda consultiva.

### 5.2 `fidelizacao.md`

- Dado acionamento `received_usage`, quando o produto for entregue, entao Livia deve abrir canal de
  cuidado e uso correto, sem vender e sem mencionar recompra como abertura.
- Dado marco `delivery_15d`, quando a cliente responder, entao o fluxo deve ramificar entre melhora,
  sem resultado ainda, uso incorreto e resposta negativa.
- Dado marco `delivery_30d_coupon`, quando a cliente tiver experiencia positiva, entao Livia deve
  perguntar experiencia primeiro, oferecer continuidade sem desconto primeiro e usar cupom de 10%
  somente como carta na manga.
- Dado cliente sem resposta, quando houver ate 3 tentativas sem retorno, entao o fluxo deve encerrar
  e remover do ativo, sem insistir.
- Deve ser acionado somente por evento operacional, nunca espontaneamente.
- Deve preservar regra: cuidado genuino gera recompra natural; nao forcar recompra.
- Deve escalar para suporte/Fernando em problema serio, reacao adversa, defeito, dano, reembolso ou
  reclamacao critica.
- Deve preservar opt-out e encerramento com dignidade.

### 5.3 `recompra.md`

- Dado cliente com memoria positiva, quando voltar para comprar, entao Livia deve reconhecer o retorno,
  confirmar dados, oferecer upsell uma vez apos confirmacao e chegar ao link em no maximo 3 turnos.
- Dado cliente sem memoria, quando pedir recompra, entao Livia deve perguntar experiencia brevemente,
  confirmar dados, oferecer upsell uma vez e chegar ao link em no maximo 3 turnos.
- Deve conter dois fluxos explicitos: com memoria positiva e sem memoria.
- Deve conter regras do upsell:
  - uma vez
  - somente apos confirmar dados
  - sem insistir se recusar
  - ancorado em pricing real
- Deve tratar problema na recompra antes de vender: acolhe, resolve ou escala suporte/Fernando, e so
  retoma recompra depois.
- Deve vender somente New Woman; Alpha Pulse deve ser orientado para Caio.
- Deve preservar checkout deterministico: Livia nao inventa link/Pix e nao coleta CPF/CEP/endereco na
  conversa se o sistema ja faz isso.

### 5.4 `suporte.md`

- Dado duvida simples, quando a cliente perguntar como tomar/ingredientes/prazo normal, entao Livia
  deve resolver em 1-2 mensagens com base na ficha tecnica/catalogo.
- Dado problema operacional, quando a cliente relatar atraso/rastreio/pedido nao chegou, entao Livia
  deve acolher, pedir dado minimo para localizar ou consultar sistema antes de prometer prazo.
- Dado problema critico, quando houver reacao adversa, alergia, mal-estar, defeito, dano, reembolso,
  devolucao, cancelamento, ameaca de Procon/processo ou humano insistente, entao Fernando deve ser
  acionado imediatamente conforme regra do prompt.
- Deve conter linha clara "Livia resolve" versus "Fernando imediato".
- Deve conter tom adaptativo por tipo de problema: duvida simples, operacional, critico.
- Deve encerrar suporte satisfeito agradecendo sem transicionar para venda, exceto se a cliente abrir
  espontaneamente recompra.
- Deve tratar cliente insatisfeita com uma tentativa genuina, Fernando se necessario, e encerramento
  com dignidade se nao houver solucao.
- Deve preservar guardrails medicos e nunca minimizar reacao adversa.

## 6. Criterios transversais

- Bloco base identico nos 4 prompts.
- Guardrails medicos/comerciais presentes em todos os prompts:
  - nunca prometer cura, milagre, garantia medica ou resultado garantido;
  - nunca inventar ingrediente, beneficio, depoimento, estatistica ou prova social;
  - nunca criar urgencia/escassez falsa;
  - nunca oferecer desconto fora das faixas aprovadas, exceto cupom de 10% do fluxo
    `delivery_30d_coupon` quando aplicavel;
  - sempre escalar Fernando em risco de saude ou problema critico;
  - sempre respeitar opt-out.
- Memoria de lead preservada em todos os agentes:
  - usa contexto anterior;
  - nao repete perguntas respondidas;
  - nao revela perfil/anotacoes;
  - trata memoria como corrigivel;
  - usa sintomas/dor apenas como cuidado, nunca como pressao.
- Transicoes coerentes entre agentes:
  - conversa ativa continua sem "oi" ou reset;
  - pausa longa retoma com referencia leve;
  - suporte resolvido so vira recompra se a cliente abrir essa porta.
- Secao de prova social permanece desativada ate Fernando enviar material validado.
- `python -m harnesses.conversation_harness --all` deve passar 10/10 apos Fase 5.
- `ruff check` e `mypy` devem ficar limpos na Fase 6, sem exigir alteracao de codigo de producao.

## 7. Dependencias criticas

- Story 035/041: checkout deterministico e confiabilidade de link/pagamento. Nao pode regredir.
- Story 044: memoria de lead ativa. Os prompts devem usar o bloco "## Memoria deste lead" sem
  comportamento invasivo.
- Story 045: `vendedor.md` e referencia canonica de estrutura/guardrails. Nao alterar.
- Story 038: follow-up automatico precisa ser verificado em producao, especialmente para fidelizacao
  sem resposta e encerramento gracioso.
- `new-woman.md`: fonte tecnica e comercial de produto, ingredientes, modo de uso, restricoes,
  prazos e pricing.
- Mega ADR de todos os agentes: fonte principal dos fluxos delta por agente.
- ADR do vendedor e pesquisa 2026: fonte para RAIA, guardrails separados, constraint persistence,
  micro-commitments e tom conversacional.
- Material Fernando/Pivatelli: fotos e prints reais sao dependencia para qualquer prova social.

## 8. Riscos e mitigacoes

| Risco | Impacto | Mitigacao |
|-------|---------|-----------|
| Falta de story Ready especifica | Bloqueia implementacao pelo framework | Criar/validar story antes da Fase 5 |
| Bloco base divergir entre prompts | Cliente percebe troca de persona | Comparacao literal na review |
| Cobranca virar nova venda | Atrito e pior conversao | Fluxo direto, sem diagnostico/upsell |
| Fidelizacao parecer spam | Opt-out e desgaste de marca | Acionamento so por evento e maximo 3 tentativas |
| Recompra insistir no upsell | Perda de confianca | Upsell uma vez, apos dados, sem insistencia |
| Suporte vender em momento critico | Risco reputacional/compliance | Resolver primeiro; Fernando imediato quando critico |
| Prova social inventada | Quebra de compliance e confianca | Secao desativada ate material real |
| Regressao checkout/memoria | Quebra de funcionalidades em producao | Harness + revisao manual contra 035/044 |

## 9. Fora de fase

- Prototipar ou alterar UI.
- Alterar router ou thresholds de roteamento.
- Criar novas ferramentas.
- Alterar banco, migracoes, infraestrutura ou deploy.
- Ativar prova social sem material validado.
- Fazer E2E WhatsApp real antes da implementacao aprovada.
