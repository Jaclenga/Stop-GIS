"""Compatibility entrypoint for the Stop-GIS Builder.

Keep this file intentionally small: deployment targets still point at
``app.py``, while the actual application lives in ``builder_app.py``.
"""

from builder_app import main
if __name__ == "__main__":
    main()
