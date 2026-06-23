-- Story-076 (fix correto): dedup do relatorio diario entre WORKERS e restarts.
-- A guarda in-process nao cobre os 2 workers uvicorn (cada processo tem seu proprio
-- estado); cada worker disparava o relatorio -> duplicata 1s a 1s no grupo.
-- Esta tabela serve de claim atomico: o primeiro worker que inserir (group_id, report_date)
-- ganha o envio; os demais veem ON CONFLICT DO NOTHING e nao enviam.
BEGIN;

CREATE TABLE IF NOT EXISTS daily_report_sent (
    group_id    TEXT        NOT NULL,
    report_date TEXT        NOT NULL,
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, report_date)
);

COMMIT;
