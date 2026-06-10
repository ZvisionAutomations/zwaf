"""base_agent: carregamento de prompt + injecao do KB de apoio."""
from __future__ import annotations

from zwaf.core.base_agent import _load_prompt


def test_vendedor_prompt_injects_persuasion_kb():
    """O KB de persuasao (vendedor.kb.md) deve ser ANEXADO ao prompt do vendedor.

    Antes a Livia so tinha o vendedor.md; o KB ficava em docs/ (nao deployado e
    nao lido). Agora o base_agent anexa {agent}.kb.md ao contexto.
    """
    prompt = _load_prompt("livia-raiz-vital", "vendedor")
    # conteudo operacional do vendedor.md continua presente
    assert "Checkout" in prompt
    # conteudo do KB de persuasao entra no contexto
    assert "KB de Persuasão" in prompt
    assert "DDPOF" in prompt


def test_agent_without_kb_loads_plain_prompt():
    """Agente sem .kb.md carrega apenas o .md, sem erro."""
    prompt = _load_prompt("livia-raiz-vital", "suporte")
    assert prompt  # carrega o suporte.md (ou default) sem quebrar
    assert "KB de Persuasão" not in prompt  # suporte nao tem KB anexo
