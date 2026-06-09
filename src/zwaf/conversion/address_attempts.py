"""Anti-loop counter for missing-address checkout attempts (story-040 FR-7).

O bug do Fernando: a cada turno o cliente reenvia o mesmo endereco, o checkout
nao completa e a Livia repete a mensagem deterministica — loop infinito sem
escala. Este modulo rastreia, POR SESSAO/LEAD, quantas vezes o checkout falhou
por dado de endereco faltante.

Persistencia entre turnos (Risco da story): o contador vive em memoria POR
PROCESSO (module-level dict), keyed por (session_id, lead_id) — sobrevive entre
requests do mesmo worker. O sink de pagamento e por-request; o contador NAO pode
morar la, senao resetaria a cada turno.

Threshold: apos a 2a falha (count >= ESCALATION_THRESHOLD) o caller deve escalar
ao humano em vez de repetir a mensagem.
"""
from __future__ import annotations

import threading

ESCALATION_THRESHOLD = 2

_lock = threading.Lock()
_attempts: dict[tuple[str, str], int] = {}


def _key(session_id: str, lead_id: str) -> tuple[str, str]:
    return (session_id or "", lead_id or "")


def record_address_failure(session_id: str, lead_id: str) -> int:
    """Registra uma falha de endereco e retorna o total acumulado na sessao."""
    key = _key(session_id, lead_id)
    with _lock:
        count = _attempts.get(key, 0) + 1
        _attempts[key] = count
        return count


def get_attempts(session_id: str, lead_id: str) -> int:
    """Retorna o numero de falhas de endereco acumuladas na sessao."""
    with _lock:
        return _attempts.get(_key(session_id, lead_id), 0)


def should_escalate(session_id: str, lead_id: str) -> bool:
    """True quando o contador atingiu o threshold de escala (>= 2)."""
    return get_attempts(session_id, lead_id) >= ESCALATION_THRESHOLD


def reset_attempts(session_id: str, lead_id: str) -> None:
    """Zera o contador (ex.: checkout concluido / endereco resolvido)."""
    with _lock:
        _attempts.pop(_key(session_id, lead_id), None)


def clear_all() -> None:
    """Limpa todo o estado — uso em testes."""
    with _lock:
        _attempts.clear()
