# Spec - Livia Pix Re-engagement Memory

> Story: `docs/stories/story-066-livia-reengagement-memory.md`
> Status: pre-implementation spec for InReview handoff.

## Objetivo

Personalizar a mensagem de reengajamento Pix usando memoria do lead ja persistida pela story-044,
mantendo fallback exato para o template atual quando nao houver memoria suficiente.

## Escopo

- Buscar memoria do lead em `leads` durante o job Pix.
- Usar `primary_symptom`, `objections`, `next_best_action` e estado do pedido apenas em runtime.
- Variar mensagem de modo controlado para objecao de preco e medo/seguranca.
- Nao logar memoria, nome, telefone completo ou sintoma.
- Manter opt-out persistente como bloqueio absoluto.

## Guardrails

- Sem cura, garantia, milagre, estatistica inventada ou promessa medica.
- Sintoma entra como cuidado leve, nao como diagnostico.
- Objecao de seguranca/medicamento direciona para orientacao segura, sem pressionar pagamento.
- Sem memoria suficiente: retorna exatamente a mensagem legada.

## Criterios de Aceite Mapeados

- AC-1: lead com `primary_symptom` e objecao `price` recebe mensagem contextual segura.
- AC-2: ausencia de memoria usa fallback legado.
- AC-3: PII descriptografada fica apenas em variavel local e nunca e logada.
- AC-4: testes cobrem personalizacao e fallback.
