"""Stop-GIS public package.

The implementation still lives partly in :mod:`shade_gis` so installations and
persisted deployments that import the former package keep working.  New code
should import from ``stop_gis``.
"""

from __future__ import annotations

from pathlib import Path

# During the compatibility window, resolve unchanged implementation modules
# (builder_imports, deployment, pages, and so on) from the legacy package.
_LEGACY_PACKAGE = Path(__file__).resolve().parent.parent / "shade_gis"
if _LEGACY_PACKAGE.is_dir():
    __path__.append(str(_LEGACY_PACKAGE))

__all__ = ["assessment_modes"]
