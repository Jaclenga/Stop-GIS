from __future__ import annotations

from pathlib import Path


def test_retired_source_package_is_absent():
    assert not Path("shade_gis").exists()


def test_repository_root_contains_only_repository_metadata():
    assert list(Path(".").glob("*.py")) == []
    assert {path.name for path in Path(".").glob("*.md")} == {"README.md"}
    assert not Path("requirements.txt").exists()
    assert not Path("pytest.ini").exists()
    assert not Path("docker-compose.yml").exists()

    for canonical_path in (
        "stop_gis/entrypoints/builder.py",
        "stop_gis/entrypoints/published.py",
        "examples/published-site/app.py",
        "infrastructure/compose.yaml",
        "infrastructure/database/schema.sql",
        "docs/CONTRIBUTING.md",
        "docs/project/AI_USE.md",
        "docs/project/CHANGELOG.md",
        "docs/project/GOVERNANCE.md",
        "docs/project/SUPPORT.md",
    ):
        assert Path(canonical_path).is_file()

    for retired_directory in ("apps", "android_app", "preview_app", "infra", "sql"):
        assert not Path(retired_directory).exists()


def test_demo_inputs_have_one_canonical_home():
    import stop_gis.builder.app as builder_app

    demo_dir = Path("data/demo").resolve()
    assert builder_app.DATA_PATH == demo_dir / "stops.txt"
    assert builder_app.SHADE_DATA_PATH == demo_dir / "shading_data.csv"
    assert builder_app.DATA_PATH.is_file()
    assert builder_app.SHADE_DATA_PATH.is_file()
    assert not Path("stops.txt").exists()
    assert not Path("shading_data.csv").exists()


def test_application_code_uses_structured_package_imports():
    source_paths = [Path("stop_gis/builder/app.py")]
    source_paths.extend(Path("stop_gis/pages").glob("*.py"))
    source_paths.extend(Path("stop_gis/builder").glob("*.py"))
    source_paths.extend(Path("stop_gis/domain").glob("*.py"))
    source_paths.extend(Path("stop_gis/ui").glob("*.py"))
    source_paths.extend(Path("stop_gis/deploy").glob("*.py"))

    for source_path in source_paths:
        source = source_path.read_text(encoding="utf-8")
        assert "from shade_gis" not in source, f"{source_path} imports the retired namespace"
        assert "import shade_gis" not in source, f"{source_path} imports the retired namespace"
