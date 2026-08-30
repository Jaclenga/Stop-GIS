"""Default Streamlit entrypoint for local Stop-GIS development.

Streamlit looks for ``streamlit_app.py`` when ``streamlit run`` is invoked
without an explicit target. Keep this wrapper aligned with ``app.py``.
"""

from builder_app import main


if __name__ == "__main__":
    main()
