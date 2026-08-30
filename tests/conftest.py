from __future__ import annotations

import copy
import os
import shutil
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stop_gis.builder.app import DEFAULT_METHODOLOGY, DEFAULT_PROJECT, DEFAULT_TAXONOMY, DEFAULT_VISUALIZATION


@pytest.fixture
def db_path() -> Path:
    directory = Path(os.environ.get("TEMP", ".")) / "shade_gis_pytest_manual" / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=True)
    try:
        yield directory / "shade-gis-test.sqlite3"
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def project() -> dict:
    data = copy.deepcopy(DEFAULT_PROJECT)
    data.update(
        {
            "name": "Test Shade Study",
            "agency": "Test Transit",
            "region": "Test City",
            "dataset_version": "test-1",
            "methodology_version": "test-1",
            "source_name": "pytest fixture",
        }
    )
    return data


@pytest.fixture
def taxonomy() -> list[dict]:
    return copy.deepcopy(DEFAULT_TAXONOMY)


@pytest.fixture
def methodology() -> dict:
    data = copy.deepcopy(DEFAULT_METHODOLOGY)
    data["summary"] = "Pytest methodology summary"
    return data


@pytest.fixture
def visualization() -> dict:
    return copy.deepcopy(DEFAULT_VISUALIZATION)


@pytest.fixture
def minimal_stops() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "stop_id": "1001",
                "stop_name": "Main St & 1st Ave",
                "stop_lat": 27.9506,
                "stop_lon": -82.4572,
                "agency": "Test Transit",
                "routes": "10",
                "municipality": "Test City",
                "shading": "No Shade",
                "review_status": "Needs Review",
                "confidence": 0.7,
                "ridership": 120,
                "context_score": 0.9,
                "context_label": "High",
                "nearby_destinations": "Hospital",
            },
            {
                "stop_id": "1002",
                "stop_name": "Central Ave",
                "stop_lat": 27.9510,
                "stop_lon": -82.4590,
                "agency": "Test Transit",
                "routes": "10",
                "municipality": "Test City",
                "shading": "Limited Shade",
                "review_status": "Unlabeled",
                "confidence": 0.9,
                "ridership": 80,
                "context_score": 0.3,
                "context_label": "Moderate",
                "nearby_destinations": "School",
            },
        ]
    )
