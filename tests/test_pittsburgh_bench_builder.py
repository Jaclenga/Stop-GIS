from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


BUILDER_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "pittsburgh_bench_inventory"
    / "build_pittsburgh_bench_seed.py"
)
SPEC = importlib.util.spec_from_file_location("pittsburgh_bench_builder", BUILDER_PATH)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_alternate_output_directory_receives_reproducibility_files(monkeypatch):
    copied = []
    monkeypatch.setattr(builder.shutil, "copy2", lambda source, target: copied.append(target))

    builder.copy_static_artifacts(Path("alternate-output"))

    assert {path.name for path in copied} == {
        "build_pittsburgh_bench_seed.py",
        "README.md",
        "DATA_LICENSE.md",
        "CITATION.md",
        "CITATION.cff",
        "stop_gis_project.json",
    }


def test_citation_and_required_attribution_are_packaged():
    package_dir = BUILDER_PATH.parent
    license_text = (package_dir / "DATA_LICENSE.md").read_text(encoding="utf-8")
    citation_text = (package_dir / "CITATION.md").read_text(encoding="utf-8")
    cff_text = (package_dir / "CITATION.cff").read_text(encoding="utf-8")

    assert "Reproduced with permission granted by Port Authority" in license_text
    assert "\u00a9" in license_text
    assert "OpenStreetMap contributors" in license_text
    assert "a29f37608eb34c3895332ff99eea9b17" in citation_text
    assert "73d1faab8d3441babcd3463ef6987559" in citation_text
    assert "accessed 2026-09-04" in citation_text
    assert "type: dataset" in cff_text
    assert "DATA_LICENSE.md" in cff_text
    assert {
        "DATA_LICENSE.md",
        "CITATION.md",
        "CITATION.cff",
        "stop_gis_project.json",
    }.issubset(builder.ZIP_MEMBERS)


def test_source_evidence_prefix_validation_is_computed(monkeypatch):
    valid = builder.validate_rows(
        [
            {
                "stop_id": "1",
                "stop_name": "A",
                "stop_lat": "40.44",
                "stop_lon": "-80.0",
                "bench_review_status": "unreviewed",
            }
        ],
        [{"mode": "BUS", "muni": "Pittsburgh city (Allegheny, PA)"}],
    )
    assert valid["source_evidence_prefixes_preserved"] is True

    monkeypatch.setattr(builder, "OUTPUT_COLUMNS", (*builder.OUTPUT_COLUMNS, "bench_tag"))
    with pytest.raises(RuntimeError, match="source-evidence schema"):
        builder.validate_rows(
            [
                {
                    "stop_id": "1",
                    "stop_name": "A",
                    "stop_lat": "40.44",
                    "stop_lon": "-80.0",
                    "bench_review_status": "unreviewed",
                }
            ],
            [{"mode": "BUS", "muni": "Pittsburgh city (Allegheny, PA)"}],
        )
