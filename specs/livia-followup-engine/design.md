# Design - Livia Follow-up Engine

## Decisao de Persistencia

Criar tabela `commercial_followups`, separada de `orders` e de `followup_events`, porque o estado e por
lead/estagio comercial, nao por pedido entregue. A chave unica sera:

```text
tenant_id + phone + stage
```

Campos principais:

- `contacts_sent`: contador persistente consumido por `build_followup_plan`.
- `next_send_at`: proximo disparo elegivel.
- `status`: `scheduled`, `sending`, `completed`, `blocked`.
- `last_template_id`, `last_temperature`, `last_sent_at`, `last_replied_at`.
- `locked_at`: claim idempotente para evitar dois workers enviando o mesmo contato.

## Candidatos

O job consulta candidatos com atividade comercial recente em:

- `conversion_events`: sinais de oferta, objecao, checkout e intencao.
- `orders`: links/pedidos nao pagos.
- `leads`: memoria comercial e objecoes.
- `lead_profiles`: opt-out persistente.

O texto passado a `build_followup_plan` e um contexto sintetico de sinais persistidos, sem PII:

- `buying_intent`, `action`, `sentiment`, `objection`, `reasons`;
- `objections` comerciais;
- estado do pedido quando houver.

## Estagios

- `post_offer`: lead com sinal comercial medio/alto, sem pedido aberto pago/linkado.
- `checkout_incomplete`: pedido `draft` ou checkout iniciado sem link pago.
- `post_link`: pedido `payment_link_created` ainda nao pago.
- `repurchase`: reservado para recompra, sem expandir politica nesta story.

## Fluxo do Job

1. Coletar candidatos elegiveis.
2. Para cada candidato, checar opt-out e risco.
3. Chamar `build_followup_plan(..., contacts_already_sent=estado.contacts_sent)`.
4. Inserir ou atualizar estado apenas se ainda houver contato permitido.
5. Ajustar `next_send_at` para janela comercial BRT quando necessario.
6. Claim atomico das linhas due com `FOR UPDATE SKIP LOCKED`.
7. Enviar via WhatsApp.
8. Em sucesso: incrementar `contacts_sent`, setar `last_sent_at`, agendar proximo ou completar.
9. Em erro: devolver para `scheduled` com pequeno retry, sem incrementar.

## Idempotencia

O claim muda `status` de `scheduled` para `sending` antes do envio. Um restart nao pega `sending`.
Isso evita duplicidade, que e o risco principal em producao. Linhas em `sending` podem exigir rotina
operacional futura de reconciliacao manual, mas nao geram spam automatico.

## Integracao API

Criar `zwaf.reporting.commercial_followup_scheduler.register_commercial_followup_scheduler` e registrar
no `lifespan` da API, junto ao scheduler Pix. No shutdown, encerrar a lista
`commercial_followup_schedulers`.

## Respostas

No `ZWAFTeam.process`, ao receber mensagem de um lead, chamar helper best-effort
`mark_followup_replied`. Se houver follow-up enviado sem resposta posterior, marcar `last_replied_at`
e emitir `FOLLOWUP_REPLIED`.
