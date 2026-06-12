---
storyId: STORY-046
verdict: CONCERNS
reviewer: Litmus (@quality-gate)
date: 2026-06-11
track: Heavy - prompts / router harness / guardrails
branch: caio/feat/livia-outros-agentes-prompts
checks:
  story_scope: PASS
  identity_block: PASS
  prompt_guardrails: PASS
  conversation_harness: PASS
  unit_tests: PASS
  lint_story_scope: PASS
  typecheck_story_scope: PASS
  lint_repo_scope: CONCERNS
  typecheck_repo_scope: CONCERNS
evidence:
  conversation_harness: ".venv\\Scripts\\python.exe -m harnesses.conversation_harness --all -> 10/10"
  unit_suite: ".venv\\Scripts\\python.exe -m pytest tests\\unit -q --basetemp C:\\Temp\\zwaf-pytest-story046 -p no:cacheprovider -> 335 passed, 1 warning"
  story_scope_lint: ".venv\\Scripts\\python.exe -m ruff check harnesses\\conversation_harness.py -> pass"
  story_scope_typecheck: ".venv\\Scripts\\python.exe -m mypy --cache-dir %TEMP%\\zwaf-mypy-story046 --no-sqlite-cache harnesses\\conversation_harness.py -> pass"
  identity_block: "IDENTITY_BLOCK_IDENTICAL=True"
issues:
  - severity: medium
    category: harness
    status: fixed
    description: >-
      Initial QA found that conversation_harness declared expected_agent and max_turns
      but did not enforce them in pass/fail. The harness now fails when the routed
      agent or turn count does not match the scenario.
    recommendation: >-
      Keep these assertions in future harness scenarios so routing regressions are
      caught instead of only reported.
  - severity: low
    category: environment
    status: accepted
    description: >-
      Pytest tmp_path/cache failed inside the Google Drive workspace and sandboxed
      temp locations with PermissionError. Full unit tests passed after running with
      an explicit basetemp under C:\Temp and pytest cache disabled.
    recommendation: >-
      Use C:\Temp\zwaf-pytest-story046 -p no:cacheprovider on this workstation, or
      fix local temp/workspace permissions.
  - severity: low
    category: pre-existing-lint
    status: accepted
    description: >-
      Repo-level ruff is not clean outside the story scope: E741 in
      src\zwaf\conversion\checkout_flow.py, F541 in tests\unit\test_payment_tool.py,
      F401 in tests\unit\test_team_checkout.py, and F401 in tests\unit\test_viacep.py.
    recommendation: >-
      Track as cleanup outside story-046. The changed harness file is ruff-clean.
  - severity: low
    category: pre-existing-typecheck
    status: accepted
    description: >-
      Repo-level mypy on src+harnesses reports pre-existing issues in
      src\zwaf\reporting\commercial_report.py, src\zwaf\memory\session.py, and
      src\zwaf\memory\lead_memory.py. The changed harness file is mypy-clean.
    recommendation: >-
      Track as cleanup outside story-046 before requiring repo-wide mypy as a hard
      gate.
---

# QA Gate Handoff - Story 046: Livia Other Agents Prompt Rewrite

**Verdict: CONCERNS.** The story implementation itself passes the required prompt,
harness, and unit-test checks. The concern is repo-level lint/type debt outside the
files changed for this story.

## Scope Reviewed

- `cobranca.md`, `fidelizacao.md`, `recompra.md`, `suporte.md`
- `harnesses/conversation_harness.py`
- `specs/livia-outros-agentes/*`
- `docs/stories/story-046-livia-outros-agentes-prompt-rewrite.md`

## Acceptance Mapping

| Acceptance criterion | Result | Evidence |
|---|---|---|
| Bloco base identico nos 4 agentes | PASS | `IDENTITY_BLOCK_IDENTICAL=True` |
| Cobranca com Pix expirado gera novo link em ate 2 turnos | PASS | Harness `cobranca_pix_expirado` |
| Fidelizacao sem resposta encerra apos 3 tentativas | PASS | Prompt + harness `fidelizacao_sem_resposta` |
| Recompra com memoria positiva chega ao link em ate 3 turnos | PASS | Prompt + harness `recompra_memoria_positiva` |
| Suporte critico aciona Fernando imediato | PASS | Prompt + harness `suporte_problema_critico` |
| Sem prova social inventada | PASS | Secoes de prova social permanecem desativadas |
| Guardrails medicos/comerciais preservados | PASS | Scan manual dos prompts |
| Harness atualizado e passando | PASS | `conversation_harness --all -> 10/10` |
| Unit regressions | PASS | `335 passed, 1 warning` |
| Repo-wide lint/type clean | CONCERNS | Pendencias pre-existentes fora do escopo |

## Commands Executed

```powershell
.venv\Scripts\python.exe -m harnesses.conversation_harness --all
# 10/10

.venv\Scripts\python.exe -m pytest tests\unit -q --basetemp C:\Temp\zwaf-pytest-story046 -p no:cacheprovider
# 335 passed, 1 warning

.venv\Scripts\python.exe -m ruff check harnesses\conversation_harness.py
# pass

.venv\Scripts\python.exe -m mypy --cache-dir %TEMP%\zwaf-mypy-story046 --no-sqlite-cache harnesses\conversation_harness.py
# pass

.venv\Scripts\python.exe -m ruff check . --no-cache
# 4 pre-existing errors outside changed files

.venv\Scripts\python.exe -m mypy --cache-dir C:\Temp\zwaf-mypy-story046-qa --no-sqlite-cache src harnesses
# 6 pre-existing errors outside changed files
```

## Final Notes

- No production Python code was changed.
- `vendedor.md` and `config.json` were not changed.
- The harness now enforces both expected agent routing and maximum turn count.
- DevOps remains blocked until operator approval; no push, PR, or deploy was done.
