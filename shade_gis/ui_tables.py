"""Shared table and pagination helpers for builder UI components."""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st


DATASET_PREVIEW_PAGE_SIZES = [25, 50, 100]


def dataframe_html(frame: pd.DataFrame, column_labels: dict[str, str] | None = None) -> str:
    """Build an escaped HTML table without Streamlit's PyArrow dataframe path."""
    if frame.columns.duplicated().any():
        duplicates = [str(column) for column in frame.columns[frame.columns.duplicated()].tolist()]
        raise ValueError(f"Display dataframe contains duplicate columns: {duplicates}")
    display = frame.rename(columns=column_labels or {})
    return display.to_html(
        index=False,
        escape=True,
        border=0,
        classes="data-page-table",
        na_rep="",
    )


def render_dataframe_table(
    frame: pd.DataFrame, column_labels: dict[str, str] | None = None
) -> None:
    if frame.empty:
        st.caption("No rows to display.")
        return
    st.markdown(
        '<div style="max-width:100%;overflow-x:auto">'
        f"{dataframe_html(frame, column_labels)}"
        "</div>",
        unsafe_allow_html=True,
    )


def dataset_preview_page(
    stops: pd.DataFrame,
    page: int,
    page_size: int,
) -> tuple[pd.DataFrame, int, int]:
    """Return only the requested slice so the UI never mounts all rows."""
    page_size = max(int(page_size), 1)
    page_count = max(1, math.ceil(len(stops) / page_size))
    safe_page = min(max(int(page), 1), page_count)
    start = (safe_page - 1) * page_size
    return stops.iloc[start : start + page_size].copy(), safe_page, page_count
