# Testes - Livia Follow-up Engine

## Unitarios

- `test_schedule_followups_creates_due_warm_post_offer`: cria estado com `next_send_at` correto.
- `test_run_job_sends_due_followup_and_persists_counter`: envia uma mensagem e incrementa
  `contacts_sent`.
- `test_run_job_does_not_resend_sending_row_after_restart`: linha `sending` nao e reenviada.
- `test_opt_out_blocks_schedule_and_send`: opt-out em DB bloqueia contato.
- `test_medical_risk_blocks_schedule_and_send`: risco medico bloqueia contato.
- `test_business_hours_rolls_to_next_window`: fora de 08:00-18:00 agenda para proxima janela.
- `test_mark_followup_replied_marks_once`: resposta apos envio marca `last_replied_at` uma vez.
- `test_scheduler_registers_hourly_job`: scheduler e registrado com `max_instances=1`.

## Regressao

Executar:

```bash
.venv\Scripts\python.exe -m pytest tests/unit -q -o cache_dir=%TEMP%\zwaf_pytest_cache
```

## Lint

Executar no pacote:

```bash
.venv\Scripts\python.exe -m ruff check .
```
