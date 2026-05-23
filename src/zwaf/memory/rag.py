"""
RAG — pgvector knowledge base (FASE 2).

Stub com interface definida. Implementação quando catálogo > 5 produtos.
"""
from __future__ import annotations


async def search_knowledge(query: str, tenant_id: str, top_k: int = 3) -> list[str]:
    """
    Busca semântica na knowledge base via pgvector.

    Fase 2 — não implementado.
    Retorna lista vazia até que o embedding pipeline seja wired.
    """
    raise NotImplementedError(
        "pgvector RAG is Phase 2 — use catalog.search_catalog() for Phase 1"
    )


async def upsert_document(
    text: str,
    source_file: str,
    tenant_id: str,
    embedding: list[float],
) -> None:
    """Insere ou atualiza chunk na knowledge base. Fase 2 — não implementado."""
    raise NotImplementedError("pgvector RAG is Phase 2")
