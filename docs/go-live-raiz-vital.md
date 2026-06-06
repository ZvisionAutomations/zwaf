# Go-Live Runbook — Raiz Vital (Lívia + Caio)

> Ordem segura para colocar os agentes WhatsApp 100% funcionais.
> Instância: `raiz-vital-zwaf-app` · EC2 `i-000571bfa6d74246b` (`c7i-flex.large`, sa-east-1a)
> Elastic IP: `56.125.161.233` · path na VPS: `/opt/zwaf` · env-file: `.env.raiz-vital`
> DB de produção: `zwaf_raiz_vital` (user `zwaf`) · domínios `api.` / `evolution.raizvitaloficial.com.br`

Cada passo é **idempotente** e o bloco 0 é **read-only**. Rode o 0 primeiro e decida o resto.

---

## Acesso à VPS — EC2 Instance Connect (NÃO precisa de `.pem`)

A chave SSH local (`raiz-vital-zwaf-key.pem`) ficou só na Lenovo. Em vez de caçá-la, use
**EC2 Instance Connect** (empurra uma chave pública temporária de 60s). A porta 22 do SG
`raiz-vital-zwaf-sg` está liberada **só para o IP da estação de trabalho** — confirme o seu.

```powershell
$aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"   # IAM user ZwafDeploy precisa de
# ec2-instance-connect:SendSSHPublicKey nesta instância (policy inline já criada)
$key = "C:\temp\ec2ic\rv_ephem"
ssh-keygen -t ed25519 -N '""' -f $key -q   # 1x; reaproveitável

# Antes de CADA ssh/scp (a chave vale 60s para novas conexões):
& $aws ec2-instance-connect send-ssh-public-key --instance-id i-000571bfa6d74246b `
  --availability-zone sa-east-1a --instance-os-user ubuntu `
  --ssh-public-key "file://$key.pub" --region sa-east-1 | Out-Null
ssh -i $key -o StrictHostKeyChecking=no ubuntu@56.125.161.233 "<comando>"
```

> Se o seu IP público mudar, atualize o ingress 22/tcp do SG `sg-07c494e675bc8249c`.
> Fallback: se tiver a `raiz-vital-zwaf-key.pem`, use `ssh -i ...key.pem ubuntu@56.125.161.233`.

---

## Estado verificado em 2026-06-06 (read-only)

| Item | Estado |
|---|---|
| Containers (caddy, evolution, postgres, redis, zwaf-api) | ✅ up/healthy |
| HTTPS público `https://api.raizvitaloficial.com.br/health` | ✅ `{"status":"ok"}` |
| Segredos no container (PII Fernet+salt, Asaas **prod**, Evolution, OpenAI, ZWAF_API_KEYS, SuperFrete) | ✅ SET |
| `GROQ_API_KEY` | ❌ MISSING (áudio sem transcrição — não bloqueia venda) |
| Migrations 001/002/003 | ✅ aplicadas |
| **Migration 004 (estoque) + código story-034** | ❌ **não deployados** (VPS roda build pré-034) |
| **Chips WhatsApp** | ❌ **zero instâncias Evolution criadas** |
| `/opt/zwaf` é repositório git? | ❗ **NÃO** — deploy é por **cópia de arquivos**, não `git pull` |

> ⚠️ Como `/opt/zwaf` não é um clone git, o deploy é feito por **scp dos arquivos** (passo 1),
> não por `git pull`. O código está mergeado no GitHub (`ZvisionAutomations/zwaf` main `75dfe35`)
> — use-o como fonte de verdade do conteúdo a copiar.

---

## 0. Pré-flight (READ-ONLY)

```bash
cd /opt/zwaf
DC="docker compose -f docker-compose.client.yml"
$DC ps                                              # A) serviços
PG=$($DC ps -q postgres); API=$($DC ps -q zwaf-api)
docker exec "$PG" psql -U zwaf -d zwaf_raiz_vital -c "\dt" | grep -E "inventory_|orders"  # B) tabelas
for V in ZWAF_PII_FERNET_KEY ASAAS_API_KEY ASAAS_BASE_URL EVOLUTION_API_KEY GROQ_API_KEY; do
  docker exec "$API" printenv "$V" >/dev/null 2>&1 && echo "$V=SET" || echo "$V=MISSING"; done  # C
docker exec "$API" sh -lc 'curl -s http://localhost:8000/health'; echo            # F) health interno
docker exec "$API" sh -lc 'curl -s http://evolution-api:8080/instance/fetchInstances -H "apikey: $EVOLUTION_API_KEY"'  # E) chips (rede interna!)
```

> Portas 8000/8080 **não** são publicadas no host (override HTTPS) — sempre cheque health/chips
> **de dentro** do container (`docker exec ... curl`), nunca via `localhost:8000` do host.

---

## 1. Atualizar o código da VPS — por CÓPIA (scp), não git

Envie os arquivos da story-034 (fonte: GitHub main `75dfe35`) para `/opt/zwaf`. Da estação:

```powershell
$ROOT = "G:\Meu Drive\Sinapse-recovey\packages\zwaf"
$files = @(
  "infra\migrations\004_inventory_reservations.sql",
  "src\zwaf\memory\inventory_store.py",
  "src\zwaf\memory\order_store.py",
  "src\zwaf\tools\payment.py",
  "src\zwaf\api\routes\payment_webhook.py",
  "harnesses\inventory_cli.py"
)
# (send-ssh-public-key antes) — empacote e envie de uma vez:
tar -czf C:\temp\s034.tgz -C $ROOT infra/migrations/004_inventory_reservations.sql `
  src/zwaf/memory/inventory_store.py src/zwaf/memory/order_store.py `
  src/zwaf/tools/payment.py src/zwaf/api/routes/payment_webhook.py harnesses/inventory_cli.py
scp -i $key C:\temp\s034.tgz ubuntu@56.125.161.233:/tmp/s034.tgz
# Na VPS: backup dos modificados, depois extrair sobre /opt/zwaf
ssh -i $key ubuntu@56.125.161.233 'cd /opt/zwaf && \
  ts=$(date +%s) && for f in src/zwaf/memory/order_store.py src/zwaf/tools/payment.py src/zwaf/api/routes/payment_webhook.py; do cp "$f" "$f.bak-034-$ts" 2>/dev/null || true; done && \
  tar -xzf /tmp/s034.tgz -C /opt/zwaf && rm -f /tmp/s034.tgz && echo "arquivos extraidos"'
```

> Antes do scp, valide no repo `packages/zwaf` que os 6 arquivos == `origin/main`
> (`git fetch && git diff origin/main -- <arquivos>`) para não deployar mudança local suja.

### 1b. Dockerfile precisa copiar `harnesses/`
O `Dockerfile` copia só `src/` e `tenants/` — **não** `harnesses/`. Sem isso o
`inventory_cli` (status/adjust/release-expired) e o cron do passo 6 não funcionam no
container. Adicione a linha (após `COPY tenants/ ./tenants/`):

```dockerfile
COPY harnesses/ ./harnesses/
```
(Envie o Dockerfile alterado junto no scp do passo 1.)

---

## 2. Aplicar a migration 004 (estoque) — MANUAL

As migrations **não rodam no boot** (o mount `/docker-entrypoint-initdb.d` só executa na 1ª
init do Postgres). Aplique à mão. **Validado em 2026-06-06**: 001→004 aplicam limpas em
pgvector com `ON_ERROR_STOP=1`.

```bash
cd /opt/zwaf
PG=$(docker compose -f docker-compose.client.yml ps -q postgres)
docker cp infra/migrations/004_inventory_reservations.sql "$PG:/tmp/004.sql"
docker exec "$PG" psql -U zwaf -d zwaf_raiz_vital -v ON_ERROR_STOP=1 -f /tmp/004.sql
docker exec "$PG" psql -U zwaf -d zwaf_raiz_vital -c "\dt inventory_*"   # 3 tabelas
```

---

## 3. Segredos no `.env.raiz-vital` (NUNCA commitar)

Quase tudo já está SET no container. Falta:

- [ ] `GROQ_API_KEY` — transcrição de áudio (opcional; sem ele, áudio do cliente não vira texto)

Já confirmados SET (não precisa mexer): `ZWAF_PII_FERNET_KEY`, `ZWAF_PII_HASH_SALT`,
`ASAAS_API_KEY` (produção), `ASAAS_BASE_URL=https://api.asaas.com/v3`,
`ASAAS_WEBHOOK_AUTH_TOKEN`, `EVOLUTION_API_KEY`, `OPENAI_API_KEY`, `ZWAF_API_KEYS`, `SUPERFRETE_*`.

---

## 4. Rebuild + recreate do `zwaf-api` (com os DOIS `-f`)

O código é **copiado** na imagem (não editable) → mudança em `src/` exige **rebuild**.
Use os dois compose files para preservar o HTTPS que está no ar (o override remove a
publicação direta de 8000/8080).

```bash
cd /opt/zwaf
docker compose -f docker-compose.client.yml -f docker-compose.https.yml \
  --env-file .env.raiz-vital build zwaf-api
docker compose -f docker-compose.client.yml -f docker-compose.https.yml \
  --env-file .env.raiz-vital up -d zwaf-api          # ~1-2 min de downtime da API

# Verificar que o código novo subiu:
API=$(docker compose -f docker-compose.client.yml ps -q zwaf-api)
docker exec "$API" python -c "import importlib.util as u; print('inventory_store', 'OK' if u.find_spec('zwaf.memory.inventory_store') else 'FALTA')"
docker exec "$API" python -c "import inspect,zwaf.tools.payment as p; print('reserva estoque', 'SIM' if 'reserve_inventory' in inspect.getsource(p) else 'NAO')"
docker exec "$API" sh -lc 'curl -s http://localhost:8000/health'; echo
```

---

## 5. Conferir estoque inicial (contagem física!)

O seed da migration entra com **new-woman=510** e **alpha-pulse=275**. A contagem física
já foi feita e confere; ajuste só se divergir.

```bash
DC="docker compose -f docker-compose.client.yml"
RUN="docker exec $($DC ps -q zwaf-api) python -m harnesses.inventory_cli"
$RUN status --tenant livia-raiz-vital
$RUN status --tenant caio-alpha-pulse
# Ajuste só se a contagem real != seed (exige motivo):
# $RUN adjust --tenant livia-raiz-vital --product new-woman --delta <N> --reason "contagem fisica" --by Fernando
```

---

## 6. Cron de liberação de reservas expiradas

```cron
*/10 * * * * cd /opt/zwaf && docker compose -f docker-compose.client.yml exec -T zwaf-api python -m harnesses.inventory_cli release-expired --tenant livia-raiz-vital
*/10 * * * * cd /opt/zwaf && docker compose -f docker-compose.client.yml exec -T zwaf-api python -m harnesses.inventory_cli release-expired --tenant caio-alpha-pulse
```

---

## 7. Conectar os chips (COM Fernando) — atualmente ZERO instâncias

O pré-flight mostrou **nenhuma instância Evolution criada** → Lívia/Caio não recebem
mensagens hoje. Criar + parear os 2 chips:

```bash
python3 -m harnesses.setup_harness --pre-qr     # health (sem Fernando)
python3 -m harnesses.setup_harness --post-qr    # gera QR -> Fernando escaneia
```

> O webhook global do compose aponta para **1 tenant** (`EVOLUTION_WEBHOOK_TENANT`). Para os
> 2 chips (Lívia + Caio), configure webhook **por instância** na Evolution, senão um dos
> agentes não recebe mensagens.

---

## 8. Webhook Asaas (por tenant, no painel Asaas)

```
https://api.raizvitaloficial.com.br/v1/webhook/payment/livia-raiz-vital
https://api.raizvitaloficial.com.br/v1/webhook/payment/caio-alpha-pulse
```
Header `asaas-access-token` = `ASAAS_WEBHOOK_AUTH_TOKEN`. (O webhook da Lívia já foi
validado E2E em produção na sessão 06-05; replicar para o do Caio.)

---

## 9. Smoke end-to-end + 1 venda real por tenant

```bash
python3 -X utf8 -m harnesses.evaluation_harness --tenant livia-raiz-vital
python3 -X utf8 -m harnesses.evaluation_harness --tenant caio-alpha-pulse
python3 -m harnesses.conversation_harness --all
python3 -m harnesses.asaas_smoke
python3 -m harnesses.asaas_webhook_e2e
```

Venda real: reserva → link Asaas → pagamento → webhook confirma estoque (`confirmed_sale`
no ledger) → marcar entrega (`harnesses/mark_delivery.py`) → follow-up agendado.

---

## 10. Go-live gradual

Ambos os tenants com `warm_up_mode: true`, `messages_per_minute: 10`. Confirme o
aquecimento dos chips antes de subir volume real.

---

## Qualidade da story-034 (gate)

- **PASS** — `docs/qa/gates/story-034-inventory-reservations-gate.md`
- 184 testes unit + ruff/mypy limpos.
- **C1 (concorrência Postgres): 30/30** em pgvector real — sem oversell (validado 06-06).
- Migrations 001→004 aplicam limpas (validado 06-06).
- C2 (CodeRabbit): follow-up não-bloqueante.

---

## Checklist final

- [ ] 0. Pré-flight rodado
- [ ] 1. Arquivos copiados (scp) + `COPY harnesses` no Dockerfile
- [ ] 2. Migration 004 aplicada
- [ ] 3. `GROQ_API_KEY` (opcional)
- [ ] 4. Rebuild+recreate (dois `-f`), código novo confirmado no container
- [ ] 5. Estoque conferido (seed 510/275)
- [ ] 6. Cron `release-expired` ativo
- [ ] 7. Chips criados + pareados (QR) + webhook por instância
- [ ] 8. Webhook Asaas por tenant
- [ ] 9. Smokes verdes + 1 venda real por tenant
- [ ] 10. Warm-up → abrir gradual

## Não fazer
- Não enviar chave Asaas/Meta/OpenAI ou código 2FA por WhatsApp.
- Não rodar `docker compose down -v`.
- Não abrir `8000/8080/5432/6379` no Security Group.
- Não mexer em `MX/TXT/SPF/DKIM/DMARC/NS` do domínio.
- Não ativar `SUPERFRETE_AUTO_CHECKOUT_ENABLED=true` antes de saldo + aprovação.
