# Spec - Livia Follow-up Engine

> Story: `docs/stories/story-065-livia-followup-engine.md`
> Status: pre-implementation spec for InReview handoff.

## Objetivo

Plugar a politica existente de follow-up comercial da Livia em runtime, sem recriar regras de
temperatura, textos ou cadencia. O motor deve usar `build_followup_plan` como fonte de verdade,
persistir estado por lead/estagio e enviar apenas quando opt-out, risco medico, limite de contatos,
janela comercial e warm-up permitirem.

## Escopo

- Criar estado persistente para follow-up comercial por `tenant_id`, `phone` e `stage`.
- Criar job agendado no startup da API, espelhando o padrao do Pix re-engagement.
- Gerar candidatos a partir de sinais persistidos em `conversion_events`, `orders`, `leads` e
  `lead_profiles`.
- Reusar `build_followup_plan`, `classify_lead_temperature`, `APPROVED_FOLLOWUP_TEMPLATES` e
  `HOT_DELAY_HOURS`.
- Emitir eventos PII-safe de `FOLLOWUP_SCHEDULED`, `FOLLOWUP_SENT` e `FOLLOWUP_REPLIED`.
- Registrar resposta ao follow-up quando uma lead responde depois de um envio.

## Fora de Escopo

- Personalizacao de texto por memoria do lead. Isso pertence a story-066.
- Mudanca da politica comercial, templates ou delays aprovados.
- A/B dos textos de follow-up.
- Deploy, push, PR ou ativacao remota.

## Guardrails

- Opt-out persistente em `lead_profiles` ou `leads` bloqueia 100% dos disparos.
- Risco medico detectado pelos sinais persistidos ou por `build_followup_plan` bloqueia 100%.
- Nenhum contato e enviado fora de 08:00-18:00 America/Sao_Paulo.
- O envio usa o `WhatsAppTool` real quando disponivel, preservando warm-up, rate limit e queue.
- Em caso de crash depois do claim e antes do stamp final, o lead fica em `sending` e nao reenvia
  automaticamente, priorizando evitar duplicidade em producao.
- Logs nao devem conter telefone completo, nome, CPF, endereco, sintoma descriptografado ou texto de
  conversa bruto.

## Criterios de Aceite Mapeados

- AC-1: candidato warm em `post_offer` recebe schedule para `last_activity_at + 1h` e envio quando
  vencido.
- AC-2: opt-out e risco medico impedem schedule/envio e sobrevivem a restart por consulta ao DB.
- AC-3: `contacts_sent` e `status` persistidos impedem reenvio apos restart.
- AC-4: eventos de funil sao emitidos para schedule, envio e resposta.
- AC-5: testes unitarios cobrem schedule, envio, idempotencia, opt-out, risco e scheduler.
