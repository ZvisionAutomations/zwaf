# QA Gate — story-040 (Livia Checkout Address Resolver)

```yaml
storyId: story-040
verdict: PASS  # CONCERNS de PII RESOLVIDO em 2026-06-22 (endereco/nome/CPF reais trocados por dados de exemplo)
reviewer: "@quality-gate (Litmus)"
date: 2026-06-08
checks:
  1_code_review: OK       # imports absolutos, ViaCEP nunca propaga excecao, contador anti-loop persiste por processo (nao por-request)
  2_unit_tests: OK        # 45/45 nos testes-alvo; suite unit completa 250 passed; ViaCEP 100% mockado (sem rede real)
  3_acceptance_criteria: OK   # AC-1..AC-8 todos com teste concreto que prova
  4_no_regressions: OK    # 035 intacta (sink/bypass CPF preservados); alteracao da fixture _ADDR_NO_DISTRICT e legitima, nao mascara regressao
  5_performance: OK       # timeout ViaCEP 3s default; await unico no caminho do webhook; nunca bloqueia alem do timeout
  6_security: OK          # [2026-06-22] PII sanitizada — endereco/nome/CPF reais substituidos por dados de exemplo (CEP 01001-000, Joao Carlos Pereira, CPF 111.444.777-35) em fixtures+prompt; NFR-4 atendido
  7_documentation: OK     # File List/DoD atualizados; prompt vendedor.md coerente (CEP+numero+few-shot, regra anti-"pequeno erro" mantida)

testResults: |
  Alvo (resolver+viacep+antiloop+035): 45 passed in 3.83s
  Suite unit completa: 250 passed, 1 warning in 31.89s
  Ambiente: C:\Temp\zwaf-venv (httpx, sem agno); PYTHONPATH=packages/zwaf/src
  ViaCEP MOCKADO em todos os testes (FakeClient / viacep_resolver injetado) — nenhuma chamada de rede real.

issues:
  - severity: medium
    category: security
    description: >
      [RESOLVIDO 2026-06-22] O endereco real do caso de producao foi copiado verbatim para fixtures de
      teste (test_address_resolver.py _FERNANDO_STYLE/_VIACEP_OK, linhas 26/29-34,
      109-178; test_checkout_deterministic_reply.py linha 19-27; test_checkout_address_antiloop.py
      linhas 31/129) e para o few-shot do prompt (tenants/livia-raiz-vital/prompts/vendedor.md
      linhas 80-83). A story (linha 21 e NFR-4) define esse endereco como PII real do
      incidente e proibe copia-lo para testes/fixtures/codigo. Os comentarios dos testes
      afirmam falsamente "CEP publico de exemplo (01001-000)... Sem PII real" enquanto o
      codigo usa o endereco real. Mitigacao: packages/zwaf/ esta gitignored (git check-ignore
      confirma) — a PII NAO entra no controle de versao; mas e deployada para a VPS de
      producao em fixtures, contrariando NFR-4. Nao ha CPF real (21722244801 ausente em todo
      o pacote) nem telefone do cliente versionado.
    recommendation: >
      Substituir o endereco real por um CEP publico de exemplo coerente (ex.: 01001-000 =
      Praca da Se / Sao Paulo / SP, que os proprios testes ja citam) em _FERNANDO_STYLE,
      _VIACEP_OK, _ADDR_OK e no few-shot do prompt; corrigir os comentarios para que deixem
      de declarar falsamente "CEP publico". Apenas troca de dado de teste/few-shot — nao
      altera logica de producao. (Nota: "Fernando" no codigo NAO e o cliente; e o
      operador/dono Raiz Vital, alvo legitimo de escala/relatorio — sem problema.)

  - severity: low
    category: tests
    description: >
      test_checkout_address_antiloop.py::test_gate_records_address_attempts_on_address_failure
      faz monkeypatch manual de pg.resolve_delivery_address com try/finally em vez de usar a
      fixture monkeypatch do pytest; funciona e restaura o original, mas e fragil a falha
      antes do finally.
    recommendation: >
      Opcional: migrar para monkeypatch.setattr para restauracao automatica. Nao-bloqueante.

notes:
  - "Anti-loop persiste corretamente entre turnos: contador mora em conversion/address_attempts.py (module-level dict + threading.Lock, keyed por (session_id, lead_id)), NAO no sink por-request. So falhas PURAMENTE de endereco alimentam o contador (_is_address_only_failure); CPF/nome faltante (035) seguem bypass literal sem escalar."
  - "ViaCEP nunca trava o checkout: timeout/HTTPStatusError/Exception generica -> None (fallback); resolve_delivery_address tem try/except defensivo extra. Verificado por test_resolve_never_raises_on_garbage e os 4 caminhos de falha de viacep."
  - "AC-7/NFR-3: 035 4/4 PASS dentro de test_checkout_deterministic_reply.py; fixture _ADDR_NO_DISTRICT omite postal_code+district de forma intencional para manter o teste offline (sem CEP -> sem ViaCEP -> district permanece faltante). Intencao do teste preservada."
```

## Mapeamento AC -> teste

| AC | Prova |
|----|-------|
| AC-1 (caso real, string completa) | test_resolve_string_with_viacep_completes_all_fields, test_resolve_full_checkout_passes_with_viacep |
| AC-2 (endereco como string) | test_normalize_string_does_not_return_empty_dict, test_parse_extracts_cep_normalized_8_digits |
| AC-3 (so CEP+numero) | test_resolve_cep_plus_number_only_via_viacep |
| AC-4 (ViaCEP fora + dados completos) | test_fallback_viacep_none_uses_llm_fields |
| AC-5 (ViaCEP fora + insuficiente) | test_fallback_viacep_none_insufficient_marks_missing, test_gate_records_address_attempts_on_address_failure |
| AC-6 (2 falhas -> escala) | test_two_address_failures_trigger_escalation_path, test_should_escalate_after_threshold, test_escalate_to_human_callable_returns_transition_message |
| AC-7 (nao-regressao 035) | test_checkout_deterministic_reply.py (4/4), test_gate_does_not_record_attempts_on_cpf_failure |
| AC-8 (CEP inexistente {"erro": true}) | test_map_viacep_erro_true_returns_none, test_resolve_cep_erro_true_returns_none |

Veredito: **CONCERNS** — checkout robusto, anti-loop correto, sem regressao, sem
secrets nem CPF/telefone real versionado. Unica pendencia: trocar o endereco real
do incidente por CEP publico de exemplo em fixtures + few-shot (NFR-4). Correcao de
dado de teste, nao de logica de producao.
