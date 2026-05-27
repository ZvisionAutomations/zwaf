"""Local Python bootstrap for running harnesses from the repo checkout."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.exists():
    src_path = str(_SRC)
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
