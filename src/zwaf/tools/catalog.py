"""Catalog Tool — consulta catálogo de produtos do tenant."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("zwaf.tools.catalog")

_TENANTS_ROOT = Path(__file__).parent.parent.parent.parent / "tenants"


async def search_catalog(query: str, tenant_id: Optional[str] = None) -> str:
    """
    Busca informações sobre produtos no catálogo do tenant.

    Fase 1: leitura de arquivos Markdown em tenants/{tenant_id}/knowledge/
    Fase 2: pgvector RAG (quando catálogo > 5 produtos)

    Args:
        query: Pergunta ou termo a buscar
        tenant_id: ID do tenant (injetado pelo contexto da chamada)

    Returns:
        Texto com informações relevantes do catálogo
    """
    if not tenant_id:
        return "Consulta de catálogo indisponível (tenant_id não informado)."

    knowledge_dir = _TENANTS_ROOT / tenant_id / "knowledge"
    if not knowledge_dir.exists():
        return "Catálogo não configurado para este tenant."

    results = []
    query_lower = query.lower()

    for md_file in knowledge_dir.glob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        # Busca simples por termos relevantes no conteúdo
        if any(term in content.lower() for term in query_lower.split()):
            # Retorna as primeiras 500 chars do arquivo relevante
            product_name = md_file.stem.replace("-", " ").title()
            results.append(f"**{product_name}**:\n{content[:500]}")

    if not results:
        return f"Nenhuma informação encontrada para '{query}' no catálogo."

    return "\n\n".join(results[:2])  # Máximo 2 produtos por consulta
