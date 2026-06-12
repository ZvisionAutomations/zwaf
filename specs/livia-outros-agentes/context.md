# Context - Reescrita dos prompts restantes da Livia

> Fase 2 do Spec Driven Development.
> Story: `docs/stories/story-046-livia-outros-agentes-prompt-rewrite.md` (Ready).
> Premissa: esta fase documenta arquitetura e relacoes. Nao implementa prompts nem altera codigo.

---

## 1. Visao de arquitetura ZWAF

O ZWAF usa um coordenador multiagente por tenant. Para a Livia/Raiz Vital:

- Tenant vivo: `tenants/livia-raiz-vital/`.
- Config do tenant: `tenants/livia-raiz-vital/config.json`.
- Prompts especializados: `tenants/livia-raiz-vital/prompts/*.md`.
- Ficha tecnica e catalogo: `tenants/livia-raiz-vital/knowledge/*.md`.
- Coordenador: `src/zwaf/core/team.py`.
- Router: `src/zwaf/core/router_agent.py`.
- Factory base dos agentes: `src/zwaf/core/base_agent.py`.
- Scheduler de fidelizacao: `src/zwaf/agents/fidelizacao.py`.

Fluxo de mensagem recebida:

1. Webhook recebe mensagem do WhatsApp.
2. `ZWAFTeam.process()` aplica guard de seguranca e opt-out.
3. Checkout deterministico pode assumir antes do LLM se houver compra confirmada ou coleta em andamento.
4. Router decide agente (`vendedor`, `recompra`, `suporte`, `cobranca`).
5. Team monta bloco de memoria de lead, se habilitado.
6. Team constrói o agente Agno correspondente e executa o prompt.
7. Webhook envia resposta e dispara atualizacao de memoria em background.

Consequencia para esta story: prompt nao deve tentar substituir router, checkout, guard ou memoria.
Prompt define comportamento conversacional dentro do agente escolhido.

## 2. Carregamento dos prompts

`src/zwaf/core/base_agent.py::_load_prompt()` carrega:

```text
tenants/{tenant_id}/prompts/{agent_name}.md
```

Se existir um arquivo `{agent_name}.kb.md` no mesmo diretorio, ele e anexado depois do prompt.
Atualmente, `vendedor.md` tem `vendedor.kb.md`; os quatro agentes desta story nao dependem de KB
propria.

`build_agent()` anexa `lead_memory_block` ao final das `instructions` quando existe memoria:

```text
prompt.md
---
agent.kb.md (se existir)
---
## Memoria deste lead (quando houver)
```

Consequencia:

- O prompt precisa instruir como usar um bloco de memoria que aparece depois dele.
- O prompt nao deve revelar que esse bloco existe.
- O bloco base identico deve morar dentro de cada prompt, a menos que surja um mecanismo real de
  partials/constantes no ZWAF. Nesta leitura, nao ha mecanismo de prompt partials.

## 3. Como cada agente se encaixa na arquitetura

### 3.1 `vendedor`

Agente canonico da story-045. Nao e alterado nesta story.

Papel arquitetural:

- default do router;
- responsavel por diagnostico, venda consultiva e fechamento inicial;
- conversa de maior risco para receita;
- base de identidade, guardrails e formato dos demais prompts.

Relacao com esta story:

- Os quatro prompts novos devem ser coerentes com o `vendedor.md`, sem duplicar a funcao de venda
  inicial.
- Transicoes para ou a partir do vendedor devem parecer continuidade da mesma Livia.

### 3.2 `cobranca`

Agente roteado por mensagem quando ha problema em pagamento ja iniciado.

Build real:

- `src/zwaf/agents/cobranca.py::build_cobranca_agent()`
- prompt: `tenants/livia-raiz-vital/prompts/cobranca.md`
- tools: WhatsApp e ferramentas de pagamento configuradas pelo agente.

Papel:

- remover atrito de pagamento;
- lidar com Pix expirado, link com erro, boleto/link nao recebido e problema de pagamento;
- agir de forma direta porque a decisao de compra ja aconteceu.

Nao e papel do cobranca:

- diagnosticar sintomas;
- iniciar checkout novo;
- fazer upsell;
- tentar recuperar objeção comercial ampla.

Ponto de risco:

- Story-035 corrigiu o caso em que "Pix" de checkout novo caia em `cobranca`. O prompt deve reforcar
  que cobranca so trata pagamento/link anterior com problema. O roteamento novo de checkout continua
  em `vendedor`/checkout deterministico.

### 3.3 `suporte`

Agente roteado por mensagem para problemas operacionais, duvidas de uso e situacoes criticas.

Build real:

- `src/zwaf/agents/suporte.py::build_suporte_agent()`
- prompt: `tenants/livia-raiz-vital/prompts/suporte.md`

Papel:

- resolver duvidas simples;
- conduzir problemas operacionais como pedido nao chegou, rastreio e prazo;
- proteger a marca em problemas criticos;
- escalar Fernando quando o risco passa do limite do agente.

Nao e papel do suporte:

- vender enquanto a cliente esta reclamando;
- insistir em recompra;
- minimizar reacao adversa;
- prometer status de pedido sem consulta/sistema.

Ponto de risco:

- Suporte deve encerrar satisfeito sem oferta ativa. Recompra so entra se a cliente abrir
  espontaneamente.

### 3.4 `recompra`

Agente roteado por mensagem de cliente recorrente ou intencao explicita de pedir de novo.

Build real:

- `src/zwaf/agents/recompra.py::build_recompra_agent()`
- prompt: `tenants/livia-raiz-vital/prompts/recompra.md`

Papel:

- converter retorno com menos friccao que venda inicial;
- usar memoria e historico com naturalidade;
- confirmar dados e quantidade;
- oferecer upsell uma vez quando fizer sentido;
- chegar ao link em no maximo 3 turnos.

Nao e papel do recompra:

- reabrir diagnostico completo;
- insistir no upsell;
- vender enquanto ha problema anterior pendente;
- tratar Alpha Pulse.

Ponto de risco:

- Memoria positiva ajuda a acelerar, mas deve sempre ser tratada como corrigivel. A Livia nao deve soar
  como sistema que vigia.

### 3.5 `fidelizacao`

Agente nao roteavel por mensagem de lead.

Build real:

- `src/zwaf/agents/fidelizacao.py::build_fidelizacao_agent()`
- scheduler: `src/zwaf/agents/fidelizacao.py::FidelizacaoScheduler`
- prompt: `tenants/livia-raiz-vital/prompts/fidelizacao.md`

Papel:

- pos-venda por evento operacional;
- acompanhar recebimento e uso;
- cuidar da experiencia;
- criar abertura natural para recompra no marco correto.

Nao e papel do fidelizacao:

- responder mensagem espontanea de lead via router;
- iniciar venda fria;
- oferecer cupom como abertura;
- continuar follow-up depois de opt-out ou ausencia prolongada.

Ponto de risco:

- O prompt deve entender mensagens internas do scheduler no formato:

```text
[FIDELIZACAO] Evento {kind} para {product_id}. Iniciar fluxo de fidelizacao conforme entrega/recebimento.
```

Hoje o config define tres passos:

- `product_received` -> `ask_usage_doubts`
- `days_after_delivery` com `days: 15` -> `ask_improvements`
- `days_after_delivery` com `days: 30` -> `recompra_coupon_10pct`

O design deve mapear isso para os marcos do ADR:

- `received_usage`
- `delivery_15d`
- `delivery_30d_coupon`

sem exigir alteracao de scheduler nesta story.

## 4. Como o router decide quando acionar cada agente

O router real esta em `src/zwaf/core/router_agent.py`.

Ordem:

1. Casos especiais determinísticos:
   - mensagem vazia, emoji ou cumprimento curto -> `vendedor`, salvo historico de compra;
   - problema de pagamento existente -> `cobranca`;
   - intencao de checkout novo/pagamento -> `vendedor`.
2. Keyword match com prioridade:
   - `cobranca`: prioridade 4
   - `suporte`: prioridade 3
   - `recompra`: prioridade 2
   - `vendedor`: prioridade 1
   - `fidelizacao`: proibido no router
3. LLM fallback, se habilitado.
4. Default: `vendedor`.

Keywords atuais em `config.json`:

| Agente | Exemplos |
|--------|----------|
| `vendedor` | `quero comprar`, `quanto custa`, `como funciona`, `pix`, `gerar link`, `fechar pedido` |
| `recompra` | `quero pedir de novo`, `acabou`, `renovar`, `segundo pote`, `recompra` |
| `suporte` | `nao chegou`, `problema`, `duvida`, `como tomar`, `efeito`, `reacao` |
| `cobranca` | `nao consegui pagar`, `link expirou`, `erro no pagamento`, `problema com pagamento` |

Regras criticas:

- `fidelizacao` nunca retorna do router; se por bug chegar em `_build_agent("fidelizacao")`,
  `team.py` redireciona para vendedor e loga warning.
- Problemas de pagamento existente ganham prioridade sobre suporte/recompra.
- Intencao de checkout novo fica com vendedor/checkout deterministico, nao cobranca.
- O prompt nao deve alterar nem assumir que pode alterar essa decisao.

## 5. Checkout deterministico e limites dos prompts

Antes do router, `ZWAFTeam._handle_checkout()` pode assumir a conversa se:

- checkout ja esta ativo;
- `analyze_message()` identifica intencao suficiente para iniciar compra/link;
- cliente esta enviando dados do fluxo deterministico.

Esse fluxo:

- coleta dados fora do prompt;
- valida campos;
- usa ViaCEP/checkout policy;
- gera Pix/link de cartao por tool;
- pode escalar em sinal critico durante checkout.

Por isso, todos os prompts devem preservar:

- nao pedir CPF, CEP, endereco ou documento diretamente;
- nao inventar Pix copia-e-cola;
- nao inventar URL;
- nao dizer que enviou link se nao houver URL real;
- nao prometer confirmacao de pagamento, rastreio ou entrega sem sistema.

## 6. Como a memoria de lead e usada

Story-044 esta habilitada em `config.json`:

```json
"lead_memory": {
  "enabled": true,
  "throttle_turns": 6,
  "summarize_last_n_runs": 12,
  "summarizer_model": "gemini-1.5-flash",
  "reinject_max_chars": 1000
}
```

Bloco dinamico:

- construido por `src/zwaf/memory/lead_memory.py::build_memory_block()`;
- anexado ao final do prompt por `team._build_lead_memory_block()` e `base_agent.build_agent()`;
- atualizado em background via `team.update_lead_memory()`.

Agentes que alimentam o summarizer:

```python
_SUMMARIZABLE_AGENTS = {"vendedor", "recompra", "suporte", "cobranca"}
```

Fidelizacao nao aparece nessa lista. Ela pode receber historico de sessao do Agno, mas nao e o foco do
summarizer de memoria na implementacao atual.

Contrato de prompt para todos:

- usar memoria como contexto natural, nao como ficha lida;
- nunca revelar "perfil", "anotacoes", "memoria" ou dados internos;
- tratar tudo como corrigivel;
- usar sintomas/dor como cuidado, nunca como pressao;
- evitar expor dado sensivel de saude de forma clinica;
- se a cliente contradiz a memoria, vale o que ela disse agora.

Uso por agente:

| Agente | Uso principal da memoria |
|--------|--------------------------|
| `cobranca` | Retomar pedido/link/quantidade/forma de pagamento em aberto para destravar pagamento |
| `suporte` | Evitar que cliente repita contexto de pedido/compra, resolver melhor e sem vender |
| `recompra` | Acelerar recompra com historico positivo, ultimo kit, quantidade e dores como cuidado |
| `fidelizacao` | Usar contexto operacional do evento e historico de conversa quando disponivel, sem depender de memoria semantica |

## 7. Relacao entre agentes

### Vendedor -> Cobranca

O vendedor conduz diagnostico, oferta e decisao. Depois que existe pagamento/link com problema,
`cobranca` remove atrito. `cobranca` nao reabre DDPOF.

### Vendedor -> Suporte

Se durante venda aparece problema operacional, reacao adversa, humano persistente, reembolso ou
reclamacao critica, `suporte`/Fernando assumem. Venda pausa.

### Vendedor -> Recompra

Se a cliente ja comprou e volta pedindo mais, `recompra` deve ser mais rapido que vendedor, porque a
confiança ja existe. Ainda preserva guardrails e checkout.

### Fidelizacao -> Recompra

Fidelizacao cria relacionamento e identifica abertura. Quando a cliente demonstra vontade de continuar
ou repor, a conversa naturalmente se torna recompra.

Contrato:

- 30 dias positivo -> oferecer continuidade;
- cupom de 10% so se houver hesitacao/preco ou abertura;
- se cliente quer comprar, `recompra` assume mentalmente o fechamento rapido.

### Suporte -> Recompra

Suporte resolvido com satisfacao pode abrir porta para recompra apenas se a cliente pedir ou sinalizar.
Sem abertura explicita, suporte encerra.

### Recompra -> Suporte

Se a cliente volta para comprar mas relata problema do pedido anterior, recompra pausa e resolve/suporte
assume. Link so depois do problema resolvido.

### Cobranca -> Suporte/Fernando

Se o problema de pagamento persiste apos 2 tentativas ou vira reclamacao critica, escala Fernando.
Se envolve reacao adversa/reembolso/devolucao, nao e cobranca: suporte/Fernando imediato.

## 8. Implicacoes para design e testes

Design deve:

- manter os prompts em seções nomeadas;
- iniciar os quatro arquivos com o mesmo `## IDENTIDADE BASE`;
- adaptar RAIA por agente sem expor raciocinio;
- manter guardrails negativos como secao separada;
- declarar explicitamente limites de checkout/memoria/prova social.

Testes devem:

- tratar harness como contrato mock, nao como prova de LLM real;
- cobrir roteamento dos agentes atuais;
- incluir cenarios pedidos pelo operador:
  - Pix expirado -> novo link em 2 turnos;
  - fidelizacao sem resposta -> encerra apos 3 tentativas;
  - recompra com memoria positiva -> link em 3 turnos;
  - suporte critico -> Fernando imediato;
- preservar `conversation_harness --all` em 10/10;
- deixar E2E WhatsApp real para review/deploy, nao para esta fase de contexto.

