"""Compatibility import for :mod:`stop_gis.persistence.store`."""

import sys

from stop_gis.persistence import store as _implementation

sys.modules[__name__] = _implementation
