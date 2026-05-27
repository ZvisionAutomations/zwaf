"""Import shim for running ZWAF from a source checkout."""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True

_SRC_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "zwaf"
__path__ = [str(_SRC_PACKAGE)]
