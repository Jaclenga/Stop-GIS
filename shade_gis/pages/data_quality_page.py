"""Dedicated Data Quality page for the active project."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from platform_store import list_images
from shade_gis.pages.data_page import render_data_quality_dashboard


def render_data_quality_page() -> None:
    """Render publication checks separately from the Data Overview workflow."""
    st.title("Data Quality")
    project_id = str(st.session_state.get("active_project_id") or "")
    images = list_images(project_id) if project_id else pd.DataFrame()
    render_data_quality_dashboard(
        st.session_state["stops"],
        images,
        show_heading=False,
    )
