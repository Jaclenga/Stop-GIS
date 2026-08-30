"""Compatibility import for the shared public-voting implementation."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from stop_gis import public_voting as _implementation
except (ImportError, ModuleNotFoundError):  # Support running from a preview directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.modules.pop("stop_gis", None)
    from stop_gis import public_voting as _implementation

sys.modules[__name__] = _implementation
