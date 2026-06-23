# Design - Livia Pix Re-engagement Memory

## Contrato

Adicionar parametro opcional `lead_memory` em `build_reengagement_message`.

```python
build_reengagement_message(total_cents, pix_due_date, payment_url=None, lead_memory=None)
```

Quando `lead_memory` e ausente, vazio ou sem campos acionaveis, a funcao retorna a mensagem atual sem
alteracao.

## Busca de Memoria

Criar helper async em `pix_reengagement.py` que consulta `leads` com os mesmos campos da story-044:

- `primary_symptom_enc`
- `memory_summary_enc`
- `objections`
- `next_best_action`

Descriptografar usando `zwaf.security.pii.decrypt_pii`, igual ao `lead_store.get_lead_memory`.

## Regras de Mensagem

- Objecao `price`: referenciar custo/dia e frete gratis sem inventar desconto.
- Objecao de seguranca/medo: orientar resposta pelo chat para tirar duvida e lembrar que, em risco de
  saude, a cliente deve falar com medico/Fernando.
- Sintoma principal: usar frase de cuidado curta, como "lembrei que voce comentou sobre sono/calores".
- Pedido: manter preco e vencimento, porque sao o gatilho operacional do Pix.

## Compatibilidade

O job Pix deve aceitar tanto callable fake de teste quanto `WhatsAppTool` real com `send_message`.
Isso corrige o encaixe de runtime sem quebrar os testes existentes.
