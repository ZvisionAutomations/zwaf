# ZWAF Hardening - Progress

## Estado atual

- Branch repo raiz: `caio/fix/zwaf-hardening-sensores`
- Branch `packages/zwaf`: `caio/fix/zwaf-hardening-sensores`
- Sprint ativo: Sprint 2 - Seguranca de entrada (proximo)
- Mudancas pre-existentes antes deste sprint: `.env.example`, `src/zwaf/tools/whatsapp.py`

## Baseline de sensores

### S1 testes unit

Comando:

```powershell
pytest -m "not integration and not slow and not harness" -q
```

Saida real:

```text
130 passed in 34.75s
```

### S2 suite cheia

Comando:

```powershell
pytest -q
```

Saida real:

```text
130 passed in 17.51s
```

### S3 cobertura

Comando:

```powershell
pytest --cov=src/zwaf --cov-report=term-missing
```

Saida real:

```text
130 passed in 21.77s
TOTAL                                     2078   1049    50%
```

### S4 eval harness

Comando literal:

```powershell
python -m harnesses.evaluation_harness --tenant livia-raiz-vital
```

Saida real:

```text
UnicodeEncodeError: 'charmap' codec can't encode character '\u2713'
```

Comandos funcionais com UTF-8 explicito:

```powershell
python -X utf8 -m harnesses.evaluation_harness --tenant livia-raiz-vital
python -X utf8 -m harnesses.evaluation_harness --tenant caio-alpha-pulse
```

Saida real:

```text
=== RESULTADO: ✓ APROVADO ===
=== RESULTADO: ✓ APROVADO ===
```

### S5 lint

Comando:

```powershell
ruff check src tests
```

Saida real:

```text
Found 19 errors.
[*] 18 fixable with the `--fix` option (1 hidden fix can be enabled with the `--unsafe-fixes` option).
```

### S6 types

Comando:

```powershell
mypy src
```

Saida real:

```text
command timed out after 124053 milliseconds
```

### S7 secret scan

Comando literal:

```powershell
grep -rnE "(sk-|api[_-]?key|secret|token|BEGIN .*PRIVATE KEY)" src
```

Saida real:

```text
grep : O termo 'grep' nao e reconhecido como nome de cmdlet, funcao, arquivo de script ou programa operavel.
```

Comando equivalente local:

```powershell
rg -n "(sk-|api[_-]?key|secret|token|BEGIN .*PRIVATE KEY)" src
```

Saida real:

```text
Exit code 0; hits em nomes de variaveis/configuracao e regexes de masking, sem segredo real impresso.
```

## Contratos

## Sprint 1 - Sensores

- [x] Item: Adicionar `pyproject.toml` minimo para Ruff e Mypy compativel com Python 3.14 | Sensor: S5 `ruff check src tests` e S6 `mypy src` | AC: Given o repo `packages/zwaf`, When os comandos rodam localmente, Then ambos usam configuracao versionada e retornam zero erros. Veredito: PASS.
- [x] Item: Declarar dependencias de sensor em `requirements.txt` sem alterar dependencias runtime | Sensor: S5/S6 e leitura de `requirements.txt` | AC: Given ambiente novo, When dependencias de teste/dev sao instaladas, Then `ruff` e `mypy` estao disponiveis sem secrets ou chamadas externas reais. Veredito: PASS.
- [x] Item: Corrigir somente falhas objetivas reportadas por Ruff e Mypy, sem refactor cosmetico amplo | Sensor: S1 `pytest -m "not integration and not slow and not harness" -q`, S2 `pytest -q`, S5, S6 | AC: Given baseline 130 passed, When lint/type fixes sao aplicados, Then a suite permanece com pelo menos 130 passed e S5/S6 ficam verdes. Veredito: PASS.

Out of scope: guard bypasses, isolamento multi-tenant, pagamentos, frete, observabilidade/PII, resiliencia de API, mudancas em `.env`, validacao VPS/Postgres/Redis, alteracao de regra de negocio.

Fechamento: PASS para o contrato do Sprint 1. S2 verde, S5 verde, S6 verde, cobertura total manteve baseline arredondado de 50%, `src/zwaf/tools/escalation.py` ficou em 90% e `src/zwaf/tools/whatsapp.py` ficou em 86%.

Evidencia final:

```text
S1: 132 passed in 21.40s
S2: 132 passed in 17.76s
S3: 132 passed in 21.78s; TOTAL 2070 1029 50%
S5: All checks passed!
S6: Success: no issues found in 46 source files
```

Nota de validacao S7: FAIL pre-existente fora do contrato do Sprint 1 no Windows local. O comando literal `grep` nao existe neste ambiente; `where.exe grep` tambem nao encontrou binario. O equivalente `rg --count-matches` retornou hits em 9 arquivos por nomes de configuracao/regexes (`api_key`, `token`, `secret`, `sk-`), sem imprimir valores sensiveis. Mantido como blocker/backlog para a auditoria final; nao houve novo secret introduzido neste sprint.

## Log append-only

- 2026-06-05 FASE 0: `PROGRESS.md` ausente; branch dedicada criada no repo raiz e em `packages/zwaf`.
- 2026-06-05 FASE 0: S1 verde (`130 passed in 34.75s`).
- 2026-06-05 FASE 0: S2 verde (`130 passed in 17.51s`).
- 2026-06-05 FASE 0: S3 verde nos testes, cobertura total baseline 50%.
- 2026-06-05 FASE 0: S4 literal falhou por encoding cp1252; S4 com `python -X utf8` aprovou Livia e Caio.
- 2026-06-05 FASE 0: S5 falhou com 19 erros Ruff.
- 2026-06-05 FASE 0: S6 sem config expirou em 124s.
- 2026-06-05 FASE 0: S7 literal indisponivel no Windows por ausencia de `grep`; `rg` equivalente retornou hits benignos de nomes/configuracao.
- 2026-06-05 FASE 1: Adicionado `pyproject.toml` com Ruff/Mypy Python 3.14 e declarados `ruff>=0.15.14`, `mypy>=2.1.0`.
- 2026-06-05 FASE 1: Corrigidos achados Ruff objetivos e erros Mypy locais, sem alterar regra de negocio.
- 2026-06-05 FASE 3: Adicionados testes unitarios de `escalate_to_human` para manter cobertura de modulo tocado em 90%.
- 2026-06-05 FASE 2: S5 PASS (`All checks passed!`), S6 PASS (`Success: no issues found in 46 source files`).
- 2026-06-05 FASE 2: S1 PASS (`132 passed in 21.40s`), S2 PASS (`132 passed in 17.76s`), S3 PASS (`132 passed in 21.78s`, total 50%).
- 2026-06-05 FASE 2: S7 literal FAIL por `grep` ausente; `rg --count-matches` retornou 9 arquivos com hits de nomes/configuracao/regexes. Mantido em blockers/backlog.

## Backlog descoberto

- Tornar `harnesses.evaluation_harness` independente de encoding de console Windows para que o comando literal de S4 passe sem `-X utf8`.
- Reconciliar S7 literal em ambiente Windows e reduzir falsos positivos do regex operacional sem ocultar secrets reais. Evidencia atual: `grep` ausente; `rg --count-matches` aponta 9 arquivos com hits por identificadores/regexes.

## Blockers (operador/VPS)

- Migrations 002/003 e trace Langfuse real dependem de VPS/chaves reais e nao serao validados nesta sessao local.
- S7 literal depende de `grep` disponivel no ambiente local/CI ou de padronizacao do sensor para `rg`/script multiplataforma. O regex atual tambem acusa identificadores seguros como `api_key`/`token` e regexes de masking.
