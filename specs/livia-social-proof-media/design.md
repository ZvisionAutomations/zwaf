# Design - Prova Social Visual da Livia

> **Story:** `docs/stories/story-047-livia-social-proof-media.md`
> **Fase 3 do Spec Driven Development.**
> Blueprint de implementacao. Nao executar sem aprovacao do operador.

---

## 1. Principios

- Prova social visual e backend-controlled.
- O prompt decide quando oferecer ou acionar; o codigo decide o que enviar.
- `send_message` permanece intacto.
- Envio de texto e midia devem preservar ordem por numero.
- Captions vivem no catalogo, nao no LLM.
- Sem envio parcial: ou exatamente 4 fotos validas, ou falha segura.
- Guardrail medico e opt-out sempre vencem prova social.

## 2. Arquivos Esperados

Criar/alterar apos aprovacao:

| Arquivo | Acao |
|---------|------|
| `tenants/livia-raiz-vital/social-proof/catalog.json` | novo catalogo estruturado |
| `src/zwaf/tools/whatsapp.py` | adicionar `send_image`, sem mudar `send_message` |
| `src/zwaf/tools/social_proof.py` | nova tool controlada |
| `src/zwaf/agents/vendedor.py` | registrar tool somente no vendedor |
| `tenants/livia-raiz-vital/prompts/vendedor.md` | ajuste minimo no bloco de prova social |
| `harnesses/social_proof_harness.py` | harness especifico |
| `tests/unit/test_whatsapp_media.py` | testes de envio de imagem |
| `tests/unit/test_social_proof_tool.py` | testes de catalogo/tool |

Nao alterar checkout, Asaas, Super Frete, pricing, memoria, router ou outros prompts por padrao.

## 3. Catalogo

Local:

```text
tenants/livia-raiz-vital/social-proof/catalog.json
```

Schema conceitual:

```json
{
  "tenant_id": "livia-raiz-vital",
  "version": "2026-06-12",
  "sequence_size": 4,
  "assets": [
    {
      "asset_id": "livia_social_proof_001",
      "status": "inactive",
      "media_url": "",
      "media_path": "",
      "caption": "",
      "caption_approved": false,
      "approved_by": "Fernando",
      "approved_at": "",
      "source": "Fernando",
      "consent_scope": "whatsapp_commercial",
      "consent_obtained_at": "",
      "consent_note": "",
      "contains_pii": null,
      "contains_face_or_name": null,
      "pii_review": "pending",
      "claim_level": "visual_only",
      "created_at": "2026-06-12"
    }
  ]
}
```

Validacao minima para asset ativo:

- `status == "active"`;
- `caption` nao vazio;
- `caption_approved is true`;
- `approved_by == "Fernando"`;
- `approved_at` preenchido;
- `consent_scope` preenchido;
- `consent_obtained_at` preenchido;
- `pii_review` aprovado;
- `claim_level == "visual_only"`;
- exatamente um mecanismo de midia valido: `media_url` ou `media_path`, conforme estrategia aprovada.

## 4. Captions

Captions permitidas:

- curtas;
- factuais;
- aprovadas por Fernando;
- sem promessa;
- sem afirmar melhora clinica;
- sem antes/depois;
- sem quantidade de clientes;
- sem percentual;
- sem "cura", "garantia", "resultado garantido" ou "equilibrio hormonal garantido".

O LLM nao pode enviar caption livre para a tool. A tool usa somente o catalogo.

## 5. WhatsAppTool.send_image

Metodo novo:

```python
async def send_image(
    self,
    phone: str,
    media_url: str | None = None,
    media_path: str | None = None,
    caption: str = "",
    session_id: str | None = None,
    asset_id: str | None = None,
) -> ToolResult:
    ...
```

Comportamento:

- se `api_key` ausente, manter padrao noop seguro semelhante ao texto;
- validar que ha fonte de midia;
- normalizar telefone;
- checar warm-up;
- serializar por numero;
- enviar pelo endpoint real de midia;
- registrar sucesso no contador diario/rate limiter;
- retornar `ToolResult.ok({"message_id": "...", "status": "sent", "media_type": "image", "asset_id": asset_id})`;
- retornar `ToolResult.fail(...)` em falha final esperada.

Retry:

- HTTP 429 deve usar backoff especifico de 30s+;
- 5xx/rede deve usar backoff curto;
- timeout deve virar falha segura;
- logs devem conter no maximo `phone_tail`, `asset_id`, `instance`, `status`.

Payload:

- depende de smoke real da Evolution;
- nao hardcodar suposicao alem do endpoint confirmado;
- se `media_path` exigir upload/base64, encapsular conversao dentro do metodo ou helper privado.

## 6. Reuso da Queue

Hoje `MessageQueue.enqueue(phone, text, send_fn)` recebe texto.

Opcoes de design:

1. Generalizar o parametro `text` para `payload` sem quebrar chamadas existentes.
2. Adicionar metodo novo `enqueue_media(phone, payload, send_fn)`.
3. Usar o mesmo lock interno por telefone com helper privado no `WhatsAppTool`.

Preferencia: menor mudanca compativel, preservando chamadas de texto e garantindo que as 4 fotos saiam
em ordem.

## 7. Tool send_social_proof

Modulo:

```text
src/zwaf/tools/social_proof.py
```

Factory recomendada:

```python
def make_social_proof_sender(
    tenant_id: str,
    whatsapp_tool: WhatsAppTool,
) -> Callable[..., Awaitable[dict]]:
    ...
```

Tool exposta ao agente:

```python
async def send_social_proof(
    phone: str,
    session_id: str | None = None,
    asset_ids: list[str] | None = None,
    consent_confirmed: bool = False,
    trigger: str = "accepted_offer",
) -> dict:
    ...
```

Regras:

- `trigger` deve ser `explicit_request` ou `accepted_offer`;
- `accepted_offer` exige `consent_confirmed=True`;
- `explicit_request` conta como consentimento conversacional para envio;
- carregar catalogo do tenant;
- filtrar ativos validos;
- se `asset_ids` vier vazio, selecionar os 4 ativos em ordem do catalogo;
- se `asset_ids` vier preenchido, todos precisam existir, estar ativos e passar validacao;
- se a selecao final nao tiver exatamente 4, nao enviar nada;
- enviar em ordem deterministica;
- se qualquer envio falhar, parar e retornar falha segura com ids ja tentados;
- nao expor URL/caminho privado no retorno.

Retorno seguro:

```json
{
  "success": true,
  "sent_count": 4,
  "asset_ids": ["livia_social_proof_001", "livia_social_proof_002"],
  "failed_asset_id": null,
  "error": null
}
```

Em falha:

```json
{
  "success": false,
  "sent_count": 0,
  "asset_ids": [],
  "failed_asset_id": null,
  "error": "social_proof_catalog_not_ready"
}
```

## 8. Integracao no Vendedor

Em `src/zwaf/agents/vendedor.py`, adicionar a factory da tool ao array `tools`.

Exemplo conceitual:

```python
tools = [
    whatsapp_tool.send_message,
    whatsapp_tool._set_typing,
    make_catalog_search(tenant_config.tenant_id),
    make_social_proof_sender(tenant_config.tenant_id, whatsapp_tool),
    make_guarded_payment_link_generator(...),
]
```

Manter o pagamento como esta. A ordem das tools nao deve alterar comportamento deterministico do
checkout.

## 9. Ajuste no Prompt

Alterar somente `tenants/livia-raiz-vital/prompts/vendedor.md`.

Substituir a secao desativada por bloco controlado:

```text
## PROVA SOCIAL VISUAL CONTROLADA

Use prova social visual somente via tool send_social_proof.

Se a cliente pedir explicitamente prova, fotos, exemplos reais ou resultado visual, responda curto e
acione a tool.

Se ela apenas demonstrar duvida indireta, pergunte antes:
"Tenho algumas fotos reais aprovadas de clientes com o produto. Quer que eu te mande?"

Se ela aceitar, acione a tool.
Se ela recusar, nao insista.

Nunca invente depoimento, numero de clientes, percentual, antes/depois, cura, garantia ou resultado
medico. Nunca escreva legenda propria para as fotos.

Nao use prova social em medo de efeito, uso de remedio, gestacao, lactacao, reacao adversa,
reclamacao, reembolso, pedido persistente por humano ou opt-out. Nesses casos, aplique o guardrail
medico/suporte e escale Fernando quando aplicavel.
```

Mensagem de retomada apos envio:

```text
Essas sao fotos reais aprovadas que recebemos. O mais importante e entender se faz sentido para o que voce esta sentindo. Qual sintoma mais te incomoda hoje?
```

## 10. Falhas e Fallback

Se a tool falhar:

- nao tentar reenviar em loop;
- nao descrever imagens que nao foram enviadas;
- responder texto curto e seguro;
- continuar por diagnostico ou explicacao do mecanismo;
- logar sem PII.

Exemplo seguro:

```text
Tive uma instabilidade para mandar as fotos agora. Posso te explicar como o New Woman funciona e, se fizer sentido, retomamos as fotos depois.
```

## 11. Idempotencia e Anti-Duplicidade

Risco: o agente pode chamar a tool novamente apos erro ou repeticao de contexto.

Mitigacoes:

- incluir `session_id` nos logs;
- retornar status estruturado claro;
- harness deve cobrir "nao reenviar apos recusa";
- opcional: registrar em memoria volatil por request que a sequencia ja foi enviada.

Nao criar persistencia nova de lead nesta story, para nao tocar memoria.

## 12. Storage

Decisao pendente:

- URL assinada;
- URL publica controlada;
- upload/base64;
- caminho local deployado.

Requisito: o mecanismo precisa funcionar no ambiente real da Evolution API e nao expor material sem
controle alem do necessario para envio.

Enquanto storage nao for aprovado, catalogo deve permanecer com placeholders inativos.

## 13. Observabilidade

Logs permitidos:

- `event`;
- `tenant_id`;
- `session_id` se nao contiver PII;
- `phone_tail`;
- `asset_id`;
- `sent_count`;
- `status`;
- erro sanitizado.

Logs proibidos:

- telefone completo;
- URL privada/signed URL;
- caminho local sensivel;
- caption se contiver PII;
- conteudo de imagem;
- nome completo de cliente;
- CPF, email, endereco.

## 14. Gates de Implementacao

Antes de merge/deploy:

- catalogo com exatamente 4 ativos aprovados ou placeholders inativos documentados;
- unit tests de `send_image`;
- unit tests de `send_social_proof`;
- harness de prova social;
- `conversation_harness --all` sem regressao;
- smoke real de envio de 4 fotos em numero de teste;
- QA confirma zero claims proibidos e zero PII indevida.
