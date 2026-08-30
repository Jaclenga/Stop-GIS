from __future__ import annotations

import math
from numbers import Integral, Real
from typing import Any


def canonical_identifier(value: Any) -> str:
    """Normalize scalar IDs while preserving meaningful textual formatting."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, Integral):
        return str(int(value))
    if isinstance(value, Real):
        numeric = float(value)
        if not math.isfinite(numeric):
            return ""
        return str(int(numeric)) if numeric.is_integer() else str(value).strip()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            scalar = item()
        except (TypeError, ValueError):
            scalar = value
        if scalar is not value:
            return canonical_identifier(scalar)
    try:
        text = str(value).strip()
    except Exception:
        return ""
    return "" if text in {"<NA>", "nan", "NaN", "NaT"} else text
