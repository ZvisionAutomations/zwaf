"""Catalog Tool — busca produtos do tenant via closure (tenant_id embutido)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger("zwaf.tools.catalog")

_TENANTS_ROOT = Path(__file__).parent.parent.parent.parent / "tenants"


def make_catalog_search(tenant_id: str) -> Callable:
    """
    Factory: retorna uma funcao de busca de catalogo pre-configurada para o tenant.
    Use esta factory ao construir agentes — nao use search_catalog diretamente.
    """
    knowledge_dir = _TENANTS_ROOT / tenant_id / "knowledge"

    async def search_catalog(query: str) -> str:
        """
        Busca informacoes sobre produtos no catalogo da Raiz Vital.

        Args:
            query: Pergunta ou nome do produto a buscar (ex: "ingredientes New Woman", "preco Alpha Pulse")

        Returns:
            Texto com informacoes relevantes do catalogo
        """
        if not knowledge_dir.exists():
            return "Catalogo nao configurado."

        results = []
        query_lower = query.lower()
        query_terms = [t for t in query_lower.split() if len(t) > 2]

        for md_file in sorted(knowledge_dir.glob("*.md")):
            content = md_file.read_text(encoding="utf-8")
            content_lower = content.lower()

            # Score por numero de termos encontrados
            hits = sum(1 for term in query_terms if term in content_lower)
            if hits > 0:
                product_name = md_file.stem.replace("-", " ").title()
                # Retorna o arquivo inteiro para o agente ter contexto completo
                results.append((hits, f"**{product_name}**:\n{content}"))

        if not results:
            # Sem match: retorna todos os produtos resumidos
            all_products = []
            for md_file in sorted(knowledge_dir.glob("*.md")):
                content = md_file.read_text(encoding="utf-8")
                name = md_file.stem.replace("-", " ").title()
                # Primeira secao (ate o primeiro ---)
                summary = content.split("---")[0].strip()
                all_products.append(f"**{name}**:\n{summary}")
            if all_products:
                return "Produtos disponiveis:\n\n" + "\n\n".join(all_products)
            return f"Nenhuma informacao encontrada para '{query}'."

        # Ordenar por relevancia, retornar ate 2 produtos
        results.sort(key=lambda x: x[0], reverse=True)
        return "\n\n---\n\n".join(content for _, content in results[:2])

    search_catalog.__name__ = "search_catalog"
    return search_catalog