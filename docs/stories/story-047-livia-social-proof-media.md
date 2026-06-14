# Story 047 - Livia: Prova social visual aprovada com envio de fotos

**Status:** Done / Deployed
**Sprint:** Sprint 47
**Epic:** ZWAF - Go-Live Controlado WhatsApp
**Criado:** 2026-06-12
**Validado:** 2026-06-12 (@product-lead / Axis - validacao local)
**Track:** Medium
**Prioridade:** P1
**Autor:** @sprint-lead (Sync)
**Contexto base:** stories 045/046, bloqueio historico de prova social, material real enviado por Fernando

---

## Contexto

As stories 045 e 046 deixaram a prova social explicitamente desativada nos prompts da Livia ate
Fernando validar material real. Agora existem 4 fotos recebidas do Fernando que podem destravar prova
social visual no WhatsApp.

Hoje o `WhatsAppTool` envia apenas texto via Evolution API (`/message/sendText/{instance}`). A Livia
nao tem uma ferramenta controlada para enviar imagens, nem um catalogo versionado de provas sociais
aprovadas. Se o prompt simplesmente mandar "use prova social", o LLM pode inventar depoimento,
resultado, numero de clientes ou promessa medica.

## Problema

Leads com duvida de confianca perguntam coisas como:

- "Funciona mesmo?"
- "Alguem ja usou?"
- "Tem resultado?"
- "Tem prova?"

Sem prova social visual, a Livia responde so com mecanismo/ingredientes e continua parecendo
consultiva demais. Com prova social mal implementada, o risco e pior: inventar resultado, sugerir cura,
usar imagem sem consentimento ou mandar midia fora de contexto.

## Objetivo

Permitir que a Livia envie 4 fotos reais aprovadas de prova social quando a lead demonstrar interesse
ou pedir evidencia, mantendo controle total sobre:

- quais fotos podem ser enviadas;
- quais legendas podem acompanhar cada foto;
- quando enviar;
- quantas enviar;
- quais claims sao proibidos.

## Escopo

### IN

- Criar um catalogo de prova social aprovada para `livia-raiz-vital`, com 4 assets:
  - `asset_id`
  - caminho/URL privada do arquivo
  - legenda aprovada
  - status `active`
  - origem/aprovador (`Fernando`)
  - observacao de consentimento
- Adicionar suporte de envio de imagem no `WhatsAppTool`, mantendo o envio de texto atual intacto.
- Criar uma tool controlada para prova social, por exemplo `send_social_proof`.
- Integrar a tool apenas ao agente vendedor.
- Atualizar `vendedor.md` para reativar prova social com guardrails:
  - pedir/confirmar consentimento antes de mandar fotos quando a lead nao pediu explicitamente;
  - se a lead disser "sim", enviar as 4 fotos aprovadas;
  - nunca inventar depoimento, estatistica, cura, garantia ou resultado medico;
  - nunca dizer "hoje estao muito melhores" sem essa frase estar aprovada como legenda real.
- Atualizar harness com cenarios de prova social:
  - lead pergunta "funciona mesmo?" -> Livia oferece prova social visual e pede consentimento;
  - lead responde "sim" -> tool envia 4 fotos;
  - lead recusa -> Livia continua conversa sem insistir;
  - lead relata medo/medicamento/reacao -> nao envia prova social, escala Fernando quando aplicavel.
- Testes unitarios para envio de imagem via Evolution API com mock HTTP.
- Smoke em VPS/container validando que a tool seleciona exatamente 4 assets ativos.

### OUT

- Alterar pricing, checkout, Asaas, Super Frete, memoria de lead ou roteamento.
- Alterar os prompts `cobranca.md`, `fidelizacao.md`, `recompra.md`, `suporte.md`, exceto se QA exigir
  uma frase negativa de preservacao.
- Enviar foto espontaneamente na primeira mensagem.
- Enviar foto em fluxo de suporte critico, reclamacao, reembolso, reacao adversa ou opt-out.
- Comitar imagem com PII/rosto/cliente em repo publico ou sem confirmacao de consentimento.
- Inventar antes/depois, numero de clientes, percentual de melhora, diagnostico ou promessa de
  equilibrio hormonal.
- Fazer deploy sem smoke real de envio de imagem.

## Regras de Produto

1. A prova social e usada para reduzir inseguranca, nao para substituir diagnostico.
2. Se a lead pedir explicitamente prova social, a Livia pode responder com texto curto e enviar as fotos.
3. Se a lead apenas demonstrar duvida ("sera que funciona?"), a Livia deve perguntar antes:
   "Tenho algumas fotos reais aprovadas de clientes com o produto. Quer que eu te mande?"
4. Se a lead disser "sim", enviar as 4 fotos aprovadas em sequencia controlada.
5. Depois das fotos, enviar uma mensagem curta de retomada comercial, sem promessa:
   "Essas sao fotos reais aprovadas que recebemos. O mais importante e entender se faz sentido para o que voce esta sentindo. Qual sintoma mais te incomoda hoje?"
6. Se a lead disser "nao", nao insistir.
7. Se a lead estiver em medo medico, uso de medicamento, gestacao/lactacao, reacao adversa ou problema
   critico, nao usar prova social como contorno; aplicar guardrail medico/Fernando.

## Acceptance Criteria

```gherkin
DADO uma lead que pergunta "funciona mesmo?"
QUANDO a Livia responder
ENTAO ela nao inventa depoimento nem estatistica
  E oferece enviar fotos reais aprovadas
  E pede consentimento antes se a lead ainda nao pediu fotos explicitamente

DADO uma lead que aceitou receber prova social visual
QUANDO a tool de prova social for executada
ENTAO exatamente 4 fotos ativas do catalogo aprovado sao enviadas
  E cada foto usa legenda aprovada
  E a Livia envia uma mensagem curta de retomada comercial apos a sequencia

DADO uma lead que recusa receber fotos
QUANDO a Livia continuar a conversa
ENTAO ela nao insiste na prova social
  E volta para diagnostico, explicacao ou fechamento conforme contexto

DADO uma lead com medo de efeito, uso de remedio ou reacao adversa
QUANDO houver tentativa de usar prova social
ENTAO a Livia nao envia fotos
  E aplica o guardrail medico
  E escala Fernando quando houver risco

DADO uma falha da Evolution API ao enviar uma foto
QUANDO a tool executar
ENTAO a falha e logada sem PII
  E a conversa nao quebra
  E a Livia segue com uma resposta textual segura

DADO a entrega da story
QUANDO os testes rodarem
ENTAO unit tests de envio de imagem passam
  E `conversation_harness --all` continua 10/10
  E harness especifico de prova social passa
```

## Dev Technical Guidance

- Confirmar endpoint exato da Evolution API em producao antes de implementar.
  - Candidatos comuns: `message/sendMedia/{instance}` ou endpoint equivalente.
  - Nao assumir payload sem smoke em VPS.
- `src/zwaf/tools/whatsapp.py` deve ganhar metodo novo, sem alterar contrato de `send_message`.
- A tool de prova social deve aceitar `phone`, `session_id`, `asset_ids` opcionais e usar apenas assets
  ativos do catalogo aprovado.
- O catalogo deve ser estruturado, nao texto solto em prompt.
- As imagens devem ficar em armazenamento privado/controlado. Se forem colocadas no workspace para
  deploy, usar pasta dedicada e regra clara para nao versionar material sensivel sem consentimento.
- Legendas devem ser curtas, aprovadas e sem promessa medica.
- O prompt do vendedor deve apenas instruir QUANDO usar a tool; quem decide os arquivos e legendas e o
  backend.

## Proposta de Arquivos

- `docs/stories/story-047-livia-social-proof-media.md`
- `specs/livia-social-proof-media/spec.md`
- `specs/livia-social-proof-media/context.md`
- `specs/livia-social-proof-media/design.md`
- `specs/livia-social-proof-media/testes.md`
- `tenants/livia-raiz-vital/social-proof/catalog.json`
- `src/zwaf/tools/whatsapp.py`
- `src/zwaf/tools/social_proof.py`
- `src/zwaf/agents/vendedor.py`
- `tenants/livia-raiz-vital/prompts/vendedor.md`
- `harnesses/social_proof_harness.py`
- `tests/unit/test_whatsapp_media.py`
- `tests/unit/test_social_proof_tool.py`

## Tasks / Subtasks

- [x] @sprint-lead: criar story da prova social visual.
- [x] @product-lead: validar valor, riscos e limites de claim.
- [x] @architect: criar specs SDD antes da implementacao.
- [x] @developer: implementar envio de midia + catalogo + tool + prompt.
- [x] @quality-gate: revisar consentimento, claims, PII, mocks da Evolution e harness.
- [x] @devops: deploy na VPS e validar health/catalogo/harness dry-run.
- [x] @devops: smoke real de envio de uma sequencia de 4 fotos para numero de teste autorizado.

## Risk Assessment

| Risco | Impacto | Mitigacao |
|-------|---------|-----------|
| Foto sem consentimento de uso | Risco legal/reputacional | Campo de consentimento/aprovador no catalogo; bloqueio se nao aprovado |
| LLM inventar resultado | Compliance e quebra de confianca | Legendas aprovadas + tool controlada + prompt negativo |
| Envio de midia quebrar WhatsApp | Conversa trava | Fallback textual e teste mock/real da Evolution |
| Spam visual | Lead se incomoda | Pedir consentimento; enviar somente em contexto de prova social |
| Uso em medo medico | Pode induzir decisao indevida | Guardrail medico bloqueia prova social e escala Fernando |
| Versionar PII visual | Exposicao de cliente | Armazenamento privado e checklist de consentimento antes do commit/deploy |

## Definition of Done

- [x] Specs SDD criadas e aprovadas.
- [x] 4 fotos aprovadas cadastradas no catalogo operacional da VPS com legendas aprovadas.
- [x] `WhatsAppTool` envia imagem por Evolution API com teste unitario.
- [x] Tool `send_social_proof` envia exatamente 4 assets ativos.
- [x] `vendedor.md` reativado com prova social controlada e sem claims inventados.
- [x] Harness cobre consentimento, aceite, catalogo incompleto, claim proibido e falha de envio.
- [x] `conversation_harness --all` segue 10/10.
- [x] Smoke na VPS envia fotos reais para numero de teste.
- [x] Nenhuma legenda com claim medico indevido.

## Product Lead Validation

**Validador:** @product-lead (Axis)  
**Data:** 2026-06-12  
**Veredito:** GO / Ready

Checklist:

- Valor comercial claro: prova visual reduz inseguranca e aumenta confianca em leads de menopausa.
- Escopo preserva checkout, memoria, router e agentes pos-venda.
- Risco de claim medico tratado como bloqueador.
- Consentimento e aprovacao dos assets sao pre-condicoes de implementacao.
- Story e testavel por unit tests, harness e smoke real na VPS.

## Nota Operacional

Antes da implementacao, coletar do operador/Fernando:

- os 4 arquivos finais das fotos;
- permissao de uso comercial no WhatsApp;
- legenda aprovada para cada foto;
- confirmacao se pode aparecer rosto/nome/marca/embalagem;
- se alguma foto exige corte/anonimizacao antes de uso.

## Change Log

| Data | Agente | Acao |
|------|--------|------|
| 2026-06-12 | @sprint-lead / Sync | Story criada para prova social visual com fotos aprovadas |
| 2026-06-12 | @product-lead / Axis | Story validada como Ready com restricoes de consentimento e claim medico |
| 2026-06-13 | @developer / Pixel | Implementado `send_image`, `send_social_proof`, catalogo placeholder local, prompt e testes |
| 2026-06-13 | @quality-gate / Litmus | Validado com 36 unit tests, conversation harness 10/10 e social proof dry-run |
| 2026-06-13 | @devops / Pipeline | Deploy aplicado na VPS; `zwaf-api` healthy; catalogo operacional ativo com 4 assets; smoke real pendente numero de teste |
| 2026-06-13 | @devops / Pipeline | Smoke real enviado para numero autorizado; tool retornou `success=True`, `sent_count=4` |
