# Testes - Livia Pix Re-engagement Memory

## Unitarios

- `test_build_reengagement_message_with_price_objection_memory`: inclui contexto e custo/dia sem
  claim proibido.
- `test_build_reengagement_message_without_memory_matches_legacy`: fallback exato.
- `test_get_lead_reengagement_memory_decrypts_runtime_only`: decifra localmente e nao loga PII.
- `test_run_pix_reengagement_job_passes_memory_to_message`: job busca memoria e envia mensagem
  personalizada.
- `test_pix_reengagement_supports_whatsapp_tool_object`: runtime aceita `send_message`.

## Regressao

Executar:

```bash
.venv\Scripts\python.exe -m pytest tests/unit -q -o cache_dir=%TEMP%\zwaf_pytest_cache
```

## Lint

Executar:

```bash
.venv\Scripts\python.exe -m ruff check .
```
