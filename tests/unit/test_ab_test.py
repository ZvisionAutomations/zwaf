"""Unit tests for A/B testing (story-056)."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch


class _FakePromptPath:
    def __init__(self, files: dict[str, str], parts: tuple[str, ...] = ()) -> None:
        self._files = files
        self._parts = parts
        self.name = parts[-1] if parts else ""

    def __truediv__(self, part: str) -> "_FakePromptPath":
        return _FakePromptPath(self._files, (*self._parts, str(part)))

    def exists(self) -> bool:
        return "/".join(self._parts) in self._files

    def read_text(self, encoding: str = "utf-8") -> str:
        return self._files["/".join(self._parts)]


class ABTestVariantTests(unittest.TestCase):
    def test_get_variant_deterministic(self) -> None:
        from zwaf.ab_testing.ab_test import get_variant

        v1 = get_variant("5511999990001", "livia-raiz-vital")
        v2 = get_variant("5511999990001", "livia-raiz-vital")

        self.assertEqual(v1, v2)
        self.assertIn(v1, ("A", "B"))

    def test_get_variant_distributes_roughly_50_50(self) -> None:
        from zwaf.ab_testing.ab_test import get_variant

        results = [get_variant(f"551199999{i:04d}", "t1") for i in range(1000)]
        count_b = results.count("B")

        self.assertGreater(count_b, 350)
        self.assertLess(count_b, 650)

    def test_load_prompt_uses_variant_b_when_file_exists(self) -> None:
        from zwaf.core.base_agent import _load_prompt

        files = {
            "t1/prompts/vendedor.md": "Prompt A content",
            "t1/prompts/vendedor_b.md": "Prompt B content",
        }
        with patch("zwaf.core.base_agent._TENANTS_ROOT", _FakePromptPath(files)):
            result = _load_prompt("t1", "vendedor", variant="B")

        self.assertEqual(result, "Prompt B content")

    def test_load_prompt_falls_back_to_a_when_b_missing(self) -> None:
        from zwaf.core.base_agent import _load_prompt

        files = {"t1/prompts/vendedor.md": "Prompt A fallback"}
        with patch("zwaf.core.base_agent._TENANTS_ROOT", _FakePromptPath(files)):
            result = _load_prompt("t1", "vendedor", variant="B")

        self.assertEqual(result, "Prompt A fallback")

    def test_admin_ab_test_no_db_returns_empty(self) -> None:
        with patch.dict("os.environ", {"DATABASE_URL": ""}):
            import importlib
            import zwaf.api.routes.ab_test as mod

            importlib.reload(mod)
            result = asyncio.run(mod.get_ab_test_metrics("vendedor_prompt", tenant_id="t1"))

        self.assertEqual(result["variants"], {})
        self.assertEqual(result["test_name"], "vendedor_prompt")


if __name__ == "__main__":
    unittest.main()
