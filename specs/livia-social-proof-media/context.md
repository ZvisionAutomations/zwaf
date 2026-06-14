# Context - Prova Social Visual da Livia

> **Story:** `docs/stories/story-047-livia-social-proof-media.md`
> **Fase 2 do Spec Driven Development.**
> Aterramento tecnico e de produto para impedir implementacao por suposicao.

---

## 1. Estado Atual

A Livia ainda nao tem prova social visual ativa.

No prompt do vendedor, a intent `proof_request` existe, mas a secao final continua desativada:
enquanto Fernando nao validasse material real, a Livia nao poderia usar fotos, prints, numeros,
depoimentos ou frases de clientes.

Agora existem 4 fotos recebidas operacionalmente, mas elas ainda dependem de:

- consentimento comercial confirmado;
- legendas aprovadas;
- revisao de PII visual;
- estrategia de armazenamento;
- validacao do endpoint real da Evolution API para midia.

## 2. Pipeline Atual de Mensagem

Fluxo simplificado:

```text
WhatsApp / Evolution
  -> api/routes/webhook.py
  -> ZWAFTeam.process()
  -> RouterAgent.route()
  -> build_vendedor_agent(...)
  -> Agno Agent com tools do vendedor
  -> ZWAFTeam envia resposta textual por WhatsAppTool.send_message()
```

O envio textual final passa por `WhatsAppTool.send_message()`, que hoje usa:

```text
/message/sendText/{instance}
```

Nao ha metodo de envio de imagem no `WhatsAppTool`.

## 3. Como o Vendedor e Construido

Arquivo: `src/zwaf/agents/vendedor.py`.

Tools atuais do vendedor:

- `whatsapp_tool.send_message`
- `whatsapp_tool._set_typing`
- `make_catalog_search(tenant_config.tenant_id)`
- `make_guarded_payment_link_generator(...)`

A Story 047 deve adicionar somente a tool controlada de prova social nessa lista.

Nao deve alterar:

- router;
- checkout deterministico;
- payment gate;
- memoria de lead;
- pricing;
- agentes de suporte, recompra, cobranca ou fidelizacao.

## 4. Como o Prompt e Montado

Arquivo: `src/zwaf/core/base_agent.py`.

Ordem das instrucoes do vendedor:

```text
tenants/livia-raiz-vital/prompts/vendedor.md
---
tenants/livia-raiz-vital/prompts/vendedor.kb.md
---
bloco dinamico "Memoria deste lead" quando existir
```

Implicacoes:

- o ajuste de prova social deve ficar no `vendedor.md`;
- o `vendedor.kb.md` ainda trata prova social como placeholder historico;
- se houver conflito, o novo bloco do `vendedor.md` deve ser mais especifico e controlado;
- a memoria do lead nao deve ser usada para pressionar com fotos nem expor dado de saude.

## 5. WhatsAppTool Atual

Arquivo: `src/zwaf/tools/whatsapp.py`.

Capacidades existentes:

- normalizacao de telefone por `_normalize_phone`;
- queue/lock por numero em `MessageQueue`;
- warm-up daily limit;
- rate limiter por numero;
- retry especifico para HTTP 429;
- retry para HTTP 5xx/rede;
- typing simulation best-effort;
- suporte a multiplas instancias/numeros.

Contrato importante:

- `send_message(phone, text, session_id=None)` nao pode mudar;
- consumidores existentes dependem de `ToolResult`;
- logs devem evitar PII e hoje usam `phone_tail`;
- a serializacao por numero deve preservar ordem.

## 6. Lacuna Tecnica

A Evolution API precisa de um endpoint/payload real para midia.

A story cita candidatos como:

```text
/message/sendMedia/{instance}
```

Mas a implementacao nao deve assumir payload sem smoke em VPS/container. O design precisa permitir
validar uma destas estrategias:

- URL assinada temporaria;
- URL publica controlada;
- upload/base64 se a Evolution aceitar;
- caminho local se o deploy/Evolution suportar leitura local.

Risco: "armazenamento privado" pode conflitar com a necessidade da Evolution acessar a midia.

## 7. Fonte Operacional das Fotos

Arquivo informado pelo operador:

```text
c:\Users\Suporte\Downloads\WhatsApp Unknown 2026-06-12 at 11.22.45.zip
```

Conteudo inspecionado sem extracao:

- `WhatsApp Image 2026-06-12 at 09.48.39.jpeg`
- `WhatsApp Image 2026-06-12 at 09.48.39 (1).jpeg`
- `WhatsApp Image 2026-06-12 at 09.48.40.jpeg`
- `WhatsApp Image 2026-06-12 at 09.48.40 (1).jpeg`

As fotos nao foram copiadas, extraidas, versionadas ou cadastradas como ativas.

## 8. KB de Prova Social Autorizada

Arquivo lido: `docs/kb/livia-social-proof-authorized.md`.

Estado:

- status `staging editorial`;
- nenhum depoimento registrado;
- uso automatico bloqueado ate `status: approved`;
- proibido registrar PII bruta como telefone, CPF/CNPJ, email, endereco, nome completo ou prints
  brutos.

Conclusao: a Story 047 deve criar catalogo proprio de assets visuais aprovados, com consentimento
verificavel. A KB existente nao autoriza uso automatico.

## 9. Guardrails de Produto e Saude

Fontes:

- `vendedor.md`;
- `vendedor.kb.md`;
- `suporte.md`;
- Story 047.

Bloqueios absolutos:

- medo de efeito;
- uso de medicamento;
- gestacao;
- lactacao;
- alergia, mal-estar ou reacao adversa;
- suporte critico;
- reclamacao, reembolso, devolucao, produto danificado;
- pedido persistente por humano;
- opt-out.

Nesses casos, prova social nao deve ser usada como argumento. A Livia deve aplicar o guardrail medico,
resolver ou escalar Fernando quando aplicavel.

## 10. Dependencias Preservadas

Checkout:

- o sistema coleta CPF/CEP/endereco por fluxo deterministico;
- a Livia nao pede PII em conversa;
- a Livia nao inventa Pix, codigo ou link;
- Story 047 nao altera isso.

Memoria:

- memoria de lead deve ser usada com naturalidade;
- nao pode expor perfil/anotacoes;
- nao pode usar dor de saude para pressionar com prova social.

Pricing:

- faixas 149 / 128 / 119,90 permanecem intactas;
- nada em prova social altera preco, desconto, frete ou condicao de pagamento.

Router:

- intent de prova social e comportamento interno do vendedor;
- nao alterar roteamento entre agentes.

## 11. Constraints de Implementacao

O backend deve ser a fonte de verdade de:

- quais imagens podem ser enviadas;
- quais captions acompanham cada imagem;
- status ativo/inativo;
- consentimento;
- ordem de envio;
- falha segura.

O LLM nunca deve receber liberdade para:

- escolher URL/caminho;
- escrever legenda livre;
- escolher quantidade de fotos;
- contornar bloqueio medico;
- enviar midia apos recusa ou opt-out.

## 12. Decisoes Pendentes

Antes de implementar envio real:

- confirmar consentimento comercial das 4 fotos;
- aprovar legenda de cada foto;
- decidir se ha corte/anonimizacao;
- decidir storage;
- validar endpoint/payload Evolution;
- decidir se o catalogo inicial tera placeholders `inactive` ou assets `active`.

Sem essas decisoes, a implementacao deve permanecer bloqueada para envio real.
