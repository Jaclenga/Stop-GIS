"""Compatibility entry point for the shared Stop-GIS public application."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from stop_gis import public_app as _implementation
except (ImportError, ModuleNotFoundError):  # Support running from a preview directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.modules.pop("stop_gis", None)
    from stop_gis import public_app as _implementation

_implementation.configure_app_dir(Path(__file__).resolve().parent)

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
