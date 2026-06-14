# Spec - Prova Social Visual da Livia

> **Story:** `docs/stories/story-047-livia-social-proof-media.md` (Ready, Track Medium)
> **Fase 1 do Spec Driven Development.**
> **Status desta spec:** aguardando aprovacao do operador antes de implementacao.
> **Agentes envolvidos:** @sprint-lead, @product-lead, @architect, @developer, @quality-gate.
> **Deploy/push:** somente com autorizacao explicita do operador via @devops.

---

## 1. Objetivo

Permitir que a Livia envie prova social visual no WhatsApp usando exatamente 4 fotos reais aprovadas,
com legendas controladas por catalogo e guardrails fortes contra invencao de depoimento, numeros,
percentuais, cura, garantia ou resultado medico.

O recurso existe para reduzir inseguranca comercial quando a lead pede evidencia ou aceita receber
fotos. Ele nao substitui diagnostico, orientacao medica, atendimento de suporte ou decisao clinica.

## 2. Valor de Produto

A prova social visual deve ajudar leads que perguntam coisas como "funciona mesmo?", "alguem ja usou?",
"tem prova?" ou "tem resultado?", sem transformar a Livia em uma agente de claims medicos.

A experiencia esperada e simples:

1. A lead pede prova social explicitamente, ou demonstra duvida indireta.
2. Se a duvida for indireta, a Livia pede permissao antes de enviar fotos.
3. Se a lead aceitar, a tool envia exatamente os 4 assets aprovados no catalogo.
4. A Livia retoma a conversa com uma pergunta consultiva curta, sem promessa.

Mensagem de retomada aprovada pela story:

> "Essas sao fotos reais aprovadas que recebemos. O mais importante e entender se faz sentido para o que voce esta sentindo. Qual sintoma mais te incomoda hoje?"

## 3. Escopo IN

- Criar catalogo estruturado em `tenants/livia-raiz-vital/social-proof/catalog.json`.
- Modelar 4 assets aprovados com `asset_id`, caminho/URL privada, legenda aprovada, status, origem,
  aprovador e observacao de consentimento.
- Adicionar metodo novo de envio de imagem no `WhatsAppTool`, preservando o contrato atual de
  `send_message`.
- Criar uma tool controlada, por exemplo `send_social_proof`, em `src/zwaf/tools/social_proof.py`.
- Integrar a tool somente ao agente vendedor em `src/zwaf/agents/vendedor.py`.
- Fazer ajuste minimo em `tenants/livia-raiz-vital/prompts/vendedor.md` para reativar prova social
  apenas com a tool e os guardrails desta story.
- Criar harness/testes para consentimento, aceite, recusa, bloqueio medico e falha de envio.
- Criar unit tests para envio de imagem via Evolution API com mock HTTP.
- Smoke em VPS/container validando que a tool seleciona exatamente 4 assets ativos.

## 4. Escopo OUT

- Alterar checkout, Asaas, Super Frete, pricing, memoria de lead, router ou fluxo de pagamento.
- Alterar `cobranca.md`, `fidelizacao.md`, `recompra.md` ou `suporte.md`, exceto se QA exigir frase
  negativa minima de preservacao.
- Alterar postura geral do `vendedor.md` alem do bloco minimo de prova social.
- Comitar imagens sem consentimento validado e sem estrategia de armazenamento aprovada.
- Enviar fotos espontaneamente na primeira mensagem.
- Enviar prova social em suporte critico, reclamacao, reembolso, reacao adversa, medo medico,
  medicamento, gestacao/lactacao ou opt-out.
- Deploy/push sem autorizacao explicita.

## 5. Contexto Lido

Arquivos lidos antes desta spec:

- `docs/stories/story-047-livia-social-proof-media.md`
- `src/zwaf/tools/whatsapp.py`
- `src/zwaf/agents/vendedor.py`
- `src/zwaf/core/base_agent.py`
- `src/zwaf/tools/base.py`
- `tenants/livia-raiz-vital/prompts/vendedor.md`
- `tenants/livia-raiz-vital/prompts/vendedor.kb.md`
- `tenants/livia-raiz-vital/prompts/suporte.md`
- `docs/kb/livia-social-proof-authorized.md`

Achados relevantes:

- `WhatsAppTool` hoje envia apenas texto por `/message/sendText/{instance}`.
- O envio atual tem queue por numero, warm-up daily limit, retry separado para HTTP 429 e retry para
  erros 5xx/rede.
- `send_message` deve permanecer intacto para nao quebrar agentes, reportes e checkout.
- O vendedor recebe tools em `build_vendedor_agent`: `send_message`, `_set_typing`, `search_catalog`
  e `generate_payment_link`.
- `vendedor.md` ainda tem prova social desativada, aguardando material real validado.
- `vendedor.kb.md` ainda trata prova social como placeholder ate validacao.
- `suporte.md` reforca que problema critico, reacao adversa, medicamento, gestacao/lactacao e opt-out
  bloqueiam venda/prova social e podem exigir Fernando.
- `docs/kb/livia-social-proof-authorized.md` esta em `staging editorial`, nao tem registros aprovados
  e proibe uso automatico ate status aprovado; as 4 fotos desta story devem entrar por catalogo novo
  com consentimento verificavel, nao por depoimento livre em KB.
- O zip operacional informado contem 4 JPEGs, mas eles nao foram extraidos nem adicionados ao repo.

## 6. Fonte Operacional das Fotos

Arquivo informado pelo operador:

`c:\Users\Suporte\Downloads\WhatsApp Unknown 2026-06-12 at 11.22.45.zip`

Conteudo inspecionado sem extracao:

- `WhatsApp Image 2026-06-12 at 09.48.39.jpeg`
- `WhatsApp Image 2026-06-12 at 09.48.39 (1).jpeg`
- `WhatsApp Image 2026-06-12 at 09.48.40.jpeg`
- `WhatsApp Image 2026-06-12 at 09.48.40 (1).jpeg`

Bloqueio: nenhuma foto deve ser extraida, versionada, enviada ou cadastrada como `active` sem
confirmacao de consentimento, legendas aprovadas e estrategia de armazenamento.

## 7. Regras de Produto

- Prova social e evidencia visual aprovada de uso real, nao evidencia medica.
- Se a lead pedir explicitamente prova social, fotos, resultado visual ou exemplos reais, a Livia pode
  acionar a tool sem nova pergunta de permissao conversacional.
- Se a lead apenas demonstrar duvida indireta, a Livia deve pedir permissao antes:
  "Tenho algumas fotos reais aprovadas de clientes com o produto. Quer que eu te mande?"
- Se a lead disser "sim", enviar as 4 fotos aprovadas em sequencia controlada.
- Se a lead disser "nao", nao insistir e continuar por diagnostico, explicacao ou fechamento conforme
  o contexto.
- Depois das fotos, enviar a mensagem curta de retomada comercial aprovada, sem promessa.
- Em medo medico, medicamento, gestacao/lactacao, reacao adversa ou problema critico, nao usar prova
  social como contorno; aplicar guardrail medico e escalar Fernando quando aplicavel.
- A precedencia de bloqueio e absoluta: `medical_risk`, `adverse_reaction`, `pregnancy`,
  `lactation`, `critical_support`, `refund`, `human_request_persistent` ou `opt_out` bloqueiam
  `send_social_proof`, mesmo se a lead tambem pedir "prova".

## 8. Regras de Compliance

A Livia nunca pode:

- inventar depoimento;
- inventar numero de clientes;
- inventar percentual de melhora ou satisfacao;
- afirmar antes/depois nao documentado;
- prometer cura, equilibrio hormonal, garantia medica ou resultado individual;
- dizer "hoje estao muito melhores" sem essa frase estar aprovada como legenda real;
- usar foto para pressionar decisao, contornar medo medico ou minimizar risco.

Falhas no envio de midia devem ser logadas sem PII e nao podem quebrar a conversa. Em caso de falha, a
Livia deve seguir com resposta textual segura, sem inventar conteudo das imagens.

## 9. Contrato do Catalogo

Arquivo esperado: `tenants/livia-raiz-vital/social-proof/catalog.json`.

Estrutura proposta:

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

Regras:

- A tool so pode enviar assets com `status: active`.
- Cada asset ativo deve ter caption aprovada e consentimento registrado.
- Cada asset ativo deve ter `approved_by: "Fernando"`, `caption_approved: true`,
  `consent_scope`, `consent_obtained_at`, `pii_review` aprovado e `claim_level: "visual_only"`.
- A sequencia valida exige exatamente 4 assets ativos.
- Se houver menos ou mais de 4 assets ativos, a tool deve falhar de modo seguro e nao enviar parcial.
- `media_url` ou `media_path` deve apontar para armazenamento privado/controlado aprovado.
- Placeholders sem consentimento devem ficar `inactive`.
- Legendas do catalogo nao podem conter claim medico, percentual, numero de clientes, antes/depois ou
  resultado garantido.

## 10. Contrato do WhatsAppTool

Adicionar metodo novo sem alterar `send_message`:

```python
async def send_image(
    self,
    phone: str,
    media_url: str | None = None,
    media_path: str | None = None,
    caption: str = "",
    session_id: str | None = None,
) -> ToolResult:
    ...
```

Regras tecnicas:

- Confirmar endpoint real da Evolution API antes da implementacao e smoke.
- Candidato da story: `/message/sendMedia/{instance}` ou equivalente.
- Reusar normalizacao de telefone, headers, timeout e logs sem PII.
- Preservar controle de warm-up/rate limit quando aplicavel a midia.
- Serializar envios por numero para manter ordem das 4 fotos.
- Tratar HTTP 429 com backoff especifico, alinhado ao envio de texto.
- Tratar 5xx/rede com retry e fallback seguro.
- Retornar `ToolResult.ok` com identificador/status quando enviado.
- Retornar `ToolResult.fail` sem levantar excecao para falhas finais esperadas.

## 11. Contrato da Tool de Prova Social

Arquivo esperado: `src/zwaf/tools/social_proof.py`.

Assinatura proposta:

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

Responsabilidades:

- Carregar catalogo do tenant `livia-raiz-vital`.
- Filtrar somente assets `active`.
- Rejeitar execucao sem consentimento conversacional confirmado, exceto quando
  `trigger == "explicit_request"`.
- Aceitar apenas triggers controlados: `explicit_request` ou `accepted_offer`.
- Se `asset_ids` for informado, usar apenas IDs ativos e aprovados, mantendo validacao de 4 assets.
- Rejeitar IDs desconhecidos, inativos, sem aprovacao ou fora do tenant.
- Enviar exatamente 4 fotos em ordem deterministica.
- Usar somente captions do catalogo.
- Nao aceitar caption livre do LLM.
- Nao aceitar caminho/URL livre do LLM.
- Nao enviar parcial se a selecao valida nao tiver exatamente 4 assets.
- Logar falhas sem numero completo, nome, caption sensivel ou conteudo de imagem.
- Retornar resumo seguro para o agente: quantidade enviada, `asset_ids`, status por asset e erro
  sanitizado. Nao retornar URLs privadas se nao for necessario.

## 12. Integracao no Vendedor

Integrar a tool somente em `src/zwaf/agents/vendedor.py`.

O prompt do vendedor deve instruir quando oferecer/acionar a prova social. O backend decide quais
arquivos e legendas enviar.

Ajuste minimo esperado em `vendedor.md`:

- substituir a secao desativada por uma secao ativa e controlada;
- manter guardrails de saude, opt-out, memoria, checkout e pricing;
- proibir claims inventados;
- instruir pedido de permissao quando a lead nao pediu fotos explicitamente;
- instruir envio das 4 fotos somente via tool;
- instruir retomada comercial curta apos a sequencia.

## 13. Criterios de Aceite

| # | Criterio | Como medir |
|---|----------|------------|
| AC1 | Lead pergunta "funciona mesmo?" e Livia nao inventa depoimento/estatistica | harness de consentimento + forbidden strings |
| AC2 | Duvida indireta gera pedido de permissao antes da midia | harness `proof_indirect_consent` |
| AC3 | Aceite explicito envia exatamente 4 fotos ativas | unit test da tool + smoke VPS/container |
| AC4 | Cada foto usa caption aprovada do catalogo | unit test `test_social_proof_tool.py` |
| AC5 | Recusa nao gera insistencia | harness `proof_declined` |
| AC6 | Medo/medicamento/reacao bloqueia prova social e aplica guardrail medico | harness `proof_medical_block` |
| AC7 | Falha da Evolution API e logada sem PII e conversa nao quebra | unit test mock HTTP |
| AC8 | `send_message` de texto continua intacto | unit/regression tests existentes de WhatsApp |
| AC9 | `conversation_harness --all` continua 10/10 | execucao local/container |
| AC10 | Nenhuma imagem e versionada sem consentimento/armazenamento aprovado | revisao git diff + checklist QA |
| AC11 | Opt-out resulta em zero midia e zero retomada comercial | harness `proof_opt_out_block` |
| AC12 | Catalogo falha com menos/mais de 4 ativos ou campos de consentimento ausentes | unit test de schema/catalogo |

## 14. Bloqueadores Antes da Implementacao

Antes de codigo alem das specs, o operador/Fernando precisa confirmar:

- permissao de uso comercial no WhatsApp para cada uma das 4 fotos;
- legenda aprovada para cada foto;
- se pode aparecer rosto, nome, marca, embalagem ou qualquer PII visual;
- se alguma foto exige corte/anonimizacao;
- estrategia de armazenamento: URL privada, pasta deployada controlada ou outro mecanismo aprovado;
- endpoint e payload reais da Evolution API para envio de imagem em producao.
- se a Evolution API aceita URL privada, URL assinada, upload/base64 ou exige URL publica acessivel.

Sem essas confirmacoes, a implementacao pode criar placeholders `inactive`, mas nao deve enviar fotos
reais nem marcar asset como `active`.

## 15. Plano de Entrega Apos Aprovacao

1. Criar `context.md`, `design.md` e `testes.md` com aterramento tecnico completo.
2. Confirmar consentimento, captions e armazenamento das 4 fotos.
3. Implementar `WhatsAppTool.send_image` com teste unitario mockado.
4. Implementar catalogo e `send_social_proof` com bloqueio para selecao diferente de 4 ativos.
5. Integrar tool no vendedor.
6. Fazer ajuste minimo no prompt do vendedor.
7. Criar/atualizar harness de prova social.
8. Rodar unit tests, harness e smoke em VPS/container.
9. Aguardar autorizacao explicita para deploy/push via @devops.

## 16. Riscos e Mitigacoes

| Risco | Impacto | Mitigacao |
|-------|---------|-----------|
| Foto sem consentimento | Legal/reputacional | catalogo exige consentimento; QA bloqueia ativos sem aprovacao |
| Claim medico inventado | Compliance e confianca | captions aprovadas + prompt negativo + tool sem caption livre |
| Envio parcial de fotos | Experiencia inconsistente | tool falha se nao houver exatamente 4 ativos |
| Evolution API payload incorreto | Midia nao envia | confirmar endpoint/payload e smoke antes de deploy |
| Storage privado inacessivel pela Evolution | Fotos falham no envio | validar URL assinada/upload/base64/storage controlado na VPS |
| Tool repetida pelo agente | Duplicidade de 4 fotos | logs por `session_id`, resultado estruturado e harness anti-reenvio |
| Spam visual | Lead incomodada | pedir consentimento em duvida indireta; respeitar recusa |
| Uso em medo medico | Decisao indevida | guardrail medico prevalece; prova social bloqueada |
| PII visual no repo | Exposicao sensivel | nao versionar imagens sem estrategia aprovada |

## 17. Decisao Pendente

Esta spec define o contrato inicial. A implementacao deve aguardar aprovacao explicita do operador
sobre:

- este `spec.md`;
- os demais documentos SDD (`context.md`, `design.md`, `testes.md`);
- consentimento e armazenamento das fotos;
- autorizacao para prosseguir com codigo.
