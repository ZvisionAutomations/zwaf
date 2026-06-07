# Story 035 - Livia checkout routing and link integrity

**Status:** Ready
**Criada:** 2026-06-06
**Origem:** conversa real exportada do WhatsApp com Livia Raiz Vital em 2026-06-06.

## Problema

Durante um checkout novo de New Woman, a cliente respondeu "Pix" depois de escolher
1 pote. O router tratou `pix` como intent de cobranca, embora ainda nao existisse
pagamento/link. O agente de cobranca coletou dados de forma imprecisa, tratou telefone
como CPF e respondeu que havia enviado link sem incluir URL.

## Escopo

- Roteamento de checkout novo por Pix deve permanecer em `vendedor`.
- `cobranca` deve ser usada para link expirado, erro de pagamento ou pagamento ja iniciado.
- Prompts devem proibir dizer "enviei o link" quando a tool nao retornou uma URL HTTP.
- A cliente deve receber pedido objetivo do dado faltante, sem loop de confirmacao.

## Acceptance Criteria

1. Mensagens como "Pix", "quero pagar via pix" e "pagar via pix" roteiam para `vendedor`.
2. Mensagens como "nao consegui pagar", "link expirou" e "erro no pagamento" roteiam para `cobranca`.
3. Mensagens sobre gerar, mandar ou enviar link de pagamento roteiam para `vendedor`, sem fallback para `suporte`.
4. O prompt de cobranca nao instrui checkout novo nem promete link sem URL.
5. O prompt de vendedor exige que resposta final contenha a URL retornada pela tool; se nao houver URL, deve dizer que houve falha e pedir o dado faltante ou escalar.
6. Depois que todos os dados obrigatorios foram coletados, confirmacoes como "sim", "confirmo" e "pode gerar o link" liberam a geracao do link; evidencias fracas nao podem voltar com loop de confirmacao.
7. CPF/CNPJ precisa passar validacao real antes da chamada ao Asaas; documento com checksum invalido deve ser tratado como dado faltante.
8. Testes unitarios cobrem a regressao do router e do gate de pagamento.

## Fora de escopo

- Mudanca nas regras de CPF/CNPJ obrigatorio para pedido real.
- Compra de etiqueta SuperFrete.
- Otimizacao ampla de conversao; isso sera tratado depois que o bot estiver funcional.

## Dev Notes

- Arquivo principal: `src/zwaf/core/router_agent.py`
- Config tenant: `tenants/livia-raiz-vital/config.json`
- Prompts: `tenants/livia-raiz-vital/prompts/vendedor.md`, `prompts/cobranca.md`
- Testes: `tests/unit/test_router_agent.py`
