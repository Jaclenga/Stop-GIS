from __future__ import annotations

from pathlib import Path


def test_retired_source_package_is_absent():
    assert not Path("shade_gis").exists()


def test_demo_inputs_have_one_canonical_home():
    import builder_app

    demo_dir = Path("data/demo").resolve()
    assert builder_app.DATA_PATH == demo_dir / "stops.txt"
    assert builder_app.SHADE_DATA_PATH == demo_dir / "shading_data.csv"
    assert builder_app.DATA_PATH.is_file()
    assert builder_app.SHADE_DATA_PATH.is_file()
    assert not Path("stops.txt").exists()
    assert not Path("shading_data.csv").exists()


def test_application_code_uses_structured_package_imports():
    source_paths = [Path("builder_app.py")]
    source_paths.extend(Path("stop_gis/pages").glob("*.py"))
    source_paths.extend(Path("stop_gis/builder").glob("*.py"))
    source_paths.extend(Path("stop_gis/domain").glob("*.py"))
    source_paths.extend(Path("stop_gis/ui").glob("*.py"))
    source_paths.extend(Path("stop_gis/deploy").glob("*.py"))

    for source_path in source_paths:
        source = source_path.read_text(encoding="utf-8")
        assert "from shade_gis" not in source, f"{source_path} imports the retired namespace"
        assert "import shade_gis" not in source, f"{source_path} imports the retired namespace"
