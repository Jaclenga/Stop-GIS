from __future__ import annotations

import subprocess
import sys

import pandas as pd
import pyarrow as pa
import pytest

from stop_gis.domain.data_quality import evaluate_data_quality
from stop_gis.ui.tables import dataframe_html


def exact_data_quality_summary() -> pd.DataFrame:
    """Construct the exact frame rendered by render_data_quality_dashboard."""
    return evaluate_data_quality(pd.DataFrame(), pd.DataFrame()).summary_table()


def test_exact_data_quality_summary_converts_to_arrow_outside_streamlit():
    summary = exact_data_quality_summary()

    table = pa.Table.from_pandas(summary, preserve_index=False)

    assert summary.shape == (5, 3)
    assert summary.columns.tolist() == ["Validation issue", "Affected records", "Status"]
    assert table.num_rows == 5


@pytest.mark.parametrize(
    "column",
    ["Validation issue", "Affected records", "Status"],
)
def test_each_data_quality_summary_column_converts_in_its_own_subprocess(column: str):
    script = """
import sys
import pandas as pd
import pyarrow as pa
from stop_gis.domain.data_quality import evaluate_data_quality

summary = evaluate_data_quality(pd.DataFrame(), pd.DataFrame()).summary_table()
for name in ["Validation issue", "Status"]:
    summary[name] = pd.Series(summary[name].tolist(), dtype=pd.StringDtype(storage="python"))
pa.Table.from_pandas(summary[[sys.argv[1]]], preserve_index=False)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, column],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, (
        f"Arrow conversion failed for data-quality column {column!r} "
        f"with return code {completed.returncode}.\n{completed.stdout}{completed.stderr}"
    )


def test_exact_data_quality_summary_renders_as_html_without_streamlit_arrow():
    summary = exact_data_quality_summary()

    rendered = dataframe_html(summary)

    assert "Validation issue" in rendered
    assert "Affected records" in rendered
    assert "Missing coordinates" in rendered
    assert "<table" in rendered


def test_dataframe_html_escapes_values_and_supports_column_labels():
    frame = pd.DataFrame({"raw_name": ["<script>alert('x')</script>"]})

    rendered = dataframe_html(frame, {"raw_name": "Safe Name"})

    assert "Safe Name" in rendered
    assert "&lt;script&gt;" in rendered
    assert "<script>" not in rendered


def test_dataframe_html_rejects_duplicate_columns():
    frame = pd.DataFrame([[1, 2]], columns=["duplicate", "duplicate"])

    with pytest.raises(ValueError, match="duplicate columns"):
        dataframe_html(frame)
