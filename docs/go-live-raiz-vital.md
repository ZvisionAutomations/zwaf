# Go-Live Runbook — Raiz Vital (Lívia + Caio)

> Ordem segura para colocar os agentes WhatsApp 100% funcionais.
> Instância: `raiz-vital-zwaf-app` · EC2 `i-000571bfa6d74246b` (`c7i-flex.large`, sa-east-1)
> Elastic IP: `56.125.161.233` · path na VPS: `/opt/zwaf` · env-file: `.env.raiz-vital`
> SSH: `ssh -i ~/.ssh/raiz-vital-zwaf-key.pem ubuntu@56.125.161.233`
> (NÃO usar `sofia-sdr-prod.pem` nem `18.228.193.55` — instância antiga, desativada.)

Cada passo é **idempotente** e o bloco 0 é **read-only** (não altera nada). Rode o 0
primeiro e decida o resto com base na saída.

> ⚠️ **Drift conhecido:** a VPS teve o `docker-compose.client.yml` editado à mão
> (SuperFrete) fora do GitHub. Antes do `git pull` (passo 1), trate as edições
> locais para o pull não falhar.

---

## 0. Pré-flight (READ-ONLY — não muda estado)

```bash
cd /opt/zwaf
DC="docker compose -f docker-compose.client.yml"

# A) Serviços rodando
$DC ps

# B) Migrations aplicadas? (procura tabelas de estoque)
docker exec -i $($DC ps -q postgres) \
  psql -U zwaf -d zwaf_raiz_vital -c "\dt" | grep -E "inventory_|orders|reservations" \
  || echo "ESTOQUE: tabelas ausentes -> aplicar migration 004 (passo 2)"

# C) Env crítico presente no container? (SET/MISSING, não imprime valor)
for V in ZWAF_PII_FERNET_KEY ZWAF_PII_HASH_SALT ASAAS_API_KEY ASAAS_BASE_URL \
         EVOLUTION_API_KEY OPENAI_API_KEY ZWAF_API_KEYS GROQ_API_KEY; do
  docker exec $($DC ps -q zwaf-api) printenv "$V" >/dev/null 2>&1 \
    && echo "$V = SET" || echo "$V = MISSING"
done

# D) Asaas é produção ou sandbox?
docker exec $($DC ps -q zwaf-api) printenv ASAAS_BASE_URL

# E) Chips conectados?
curl -s http://localhost:8080/instance/fetchInstances -H "apikey: $EVOLUTION_API_KEY" | head -c 800; echo

# F) Health da API
curl -s http://localhost:8000/health; echo

# G) Código deployado (a VPS já tem a 004?)
git -C /opt/zwaf log --oneline -3
```

---

## 1. Atualizar o código da VPS (drift-safe)

A VPS pode ter o `docker-compose.client.yml` editado localmente (SuperFrete). O fix
do repo é **superset** (SuperFrete + PII), então é seguro descartar a edição local:

```bash
cd /opt/zwaf
git fetch origin

# Ver se há edições locais não commitadas (esp. no compose)
git status --short

# Se o compose aparecer modificado, guarde a edição local antes do pull:
git stash push -m "vps-local-compose" -- docker-compose.client.yml 2>/dev/null || true

git checkout main && git pull origin main   # traz story-034 + fix do compose (PII+SuperFrete)

# Confira que o compose do repo tem as chaves PII e SuperFrete:
grep -c ZWAF_PII_FERNET_KEY docker-compose.client.yml   # esperado: 1
grep -c SUPERFRETE_TOKEN     docker-compose.client.yml   # esperado: 1
# Se OK, pode descartar o stash local: git stash drop
```

> O fix do compose adiciona `ZWAF_PII_FERNET_KEY`, `ZWAF_PII_HASH_SALT` e os
> `SUPERFRETE_*` ao container `zwaf-api` (antes não eram injetados no repo).

---

## 2. Aplicar a migration 004 (estoque) — MANUAL

As migrations **não rodam no boot**; o mount `/docker-entrypoint-initdb.d` só executa
na primeira inicialização do Postgres (volume vazio). Com banco existente, aplique à mão:

```bash
cd /opt/zwaf
DC="docker compose -f docker-compose.client.yml"
docker exec -i $($DC ps -q postgres) \
  psql -U zwaf -d zwaf_raiz_vital < infra/migrations/004_inventory_reservations.sql

# Verificar
docker exec -i $($DC ps -q postgres) \
  psql -U zwaf -d zwaf_raiz_vital -c "\dt inventory_*"
```

> Se o pré-flight (B) mostrou que 002/003 também faltam, aplique-as antes, na ordem.

---

## 3. Preencher segredos no `.env` da VPS

Edite `/opt/zwaf/.env.raiz-vital` (NUNCA commitar). Mínimo para vender:

```bash
# Gere a chave de PII NO SERVIDOR (não cole de fora):
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# -> ZWAF_PII_FERNET_KEY=<saida>
# ZWAF_PII_HASH_SALT=<string aleatoria longa, ex: openssl rand -hex 32>
```

Checklist de variáveis (ver `.env.example` para a lista completa):
- [ ] `ZWAF_PII_FERNET_KEY`, `ZWAF_PII_HASH_SALT`  ← sem isto o checkout recusa de propósito
- [ ] `ASAAS_API_KEY` (produção), `ASAAS_BASE_URL=https://api.asaas.com/v3`, `ASAAS_WEBHOOK_AUTH_TOKEN`
- [ ] `EVOLUTION_API_KEY`, `WA_NUMBER_1=5511967318916`, `WA_INSTANCE_1`
- [ ] `WA_NUMBER_2` + `WA_INSTANCE_2` (chip do Caio/Alpha Pulse)
- [ ] `OPENAI_API_KEY`, `ZWAF_API_KEYS`, `POSTGRES_PASSWORD`, `CORS_ORIGINS`
- [ ] `GROQ_API_KEY` (transcrição de áudio)
- [ ] `REPORT_WA_DEST_NUMBER` (relatório diário do Fernando)
- [ ] Opcional: `LANGFUSE_*`, `SUPERFRETE_*` (frete manual no go-live)

---

## 4. Recriar o container para pegar o novo env

```bash
cd /opt/zwaf
docker compose -f docker-compose.client.yml --env-file .env.raiz-vital up -d --build zwaf-api

# Confirmar que as chaves PII chegaram:
DC="docker compose -f docker-compose.client.yml"
docker exec $($DC ps -q zwaf-api) printenv ZWAF_PII_FERNET_KEY >/dev/null && echo "PII OK" || echo "PII FALTANDO"
```

Com HTTPS (Caddy), use também `-f docker-compose.https.yml`.

---

## 5. Conferir estoque inicial (contagem física!)

```bash
DC="docker compose -f docker-compose.client.yml"
RUN="docker exec $($DC ps -q zwaf-api) python -m harnesses.inventory_cli"

$RUN status --tenant livia-raiz-vital
$RUN status --tenant caio-alpha-pulse

# Ajustar para o número real contado pelo Fernando (exige motivo):
$RUN adjust --tenant livia-raiz-vital --product new-woman   --delta <N> --reason "contagem fisica go-live" --by Fernando
$RUN adjust --tenant caio-alpha-pulse --product alpha-pulse --delta <N> --reason "contagem fisica go-live" --by Fernando
```

---

## 6. Agendar liberação de reservas expiradas (cron)

Sem isto, reservas expiradas seguram estoque para sempre. Crontab a cada 10 min:

```cron
*/10 * * * * cd /opt/zwaf && docker compose -f docker-compose.client.yml exec -T zwaf-api python -m harnesses.inventory_cli release-expired --tenant livia-raiz-vital
*/10 * * * * cd /opt/zwaf && docker compose -f docker-compose.client.yml exec -T zwaf-api python -m harnesses.inventory_cli release-expired --tenant caio-alpha-pulse
```

---

## 7. Conectar os chips (COM Fernando)

```bash
python3 -m harnesses.setup_harness --pre-qr     # health (sem Fernando)
python3 -m harnesses.setup_harness --post-qr    # gera QR -> Fernando escaneia
```

> Webhook por instância: `EVOLUTION_WEBHOOK_TENANT` aponta para 1 tenant só. Para os
> 2 chips (Lívia + Caio), configure webhook **por instância** na Evolution, senão um
> dos agentes não recebe mensagens.

---

## 8. Registrar webhook Asaas (por tenant, no painel Asaas)

```
https://<dominio>/v1/webhook/payment/livia-raiz-vital
https://<dominio>/v1/webhook/payment/caio-alpha-pulse
```
Auth header `asaas-access-token` = `ASAAS_WEBHOOK_AUTH_TOKEN`.

---

## 9. Smoke end-to-end (antes de abrir para clientes)

```bash
python3 -X utf8 -m harnesses.evaluation_harness --tenant livia-raiz-vital
python3 -X utf8 -m harnesses.evaluation_harness --tenant caio-alpha-pulse
python3 -m harnesses.conversation_harness --all
python3 -m harnesses.asaas_smoke           # com conta real
python3 -m harnesses.asaas_webhook_e2e
```

Teste manual de 1 venda real por tenant: reserva → link Asaas → pagamento →
webhook confirma estoque (`confirmed_sale` no ledger) → marcar entrega
(`harnesses/mark_delivery.py`) → follow-up de fidelização agendado.

---

## 10. Go-live gradual

Ambos os tenants estão com `warm_up_mode: true`, `messages_per_minute: 10`.
Confirme o aquecimento dos chips concluído antes de subir volume real.

---

## Checklist final (resumo)

- [ ] 0. Pré-flight rodado e analisado
- [ ] 1. `git pull` na VPS
- [ ] 2. Migration 004 aplicada (e 002/003 se faltavam)
- [ ] 3. Segredos no `.env` (PII, Asaas prod, Evolution, OpenAI, Groq)
- [ ] 4. Container recriado, PII confirmada no container
- [ ] 5. Estoque conferido por contagem física
- [ ] 6. Cron de `release-expired` ativo
- [ ] 7. Chips conectados (QR) + webhook por instância
- [ ] 8. Webhook Asaas registrado por tenant
- [ ] 9. Smokes verdes + 1 venda real por tenant
- [ ] 10. Warm-up concluído → abrir gradual
