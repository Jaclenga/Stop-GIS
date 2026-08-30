from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


def test_core_modules_compile_without_bytecode_writes():
    for filename in [
        "stop_gis/builder/app.py",
        "stop_gis/entrypoints/__init__.py",
        "stop_gis/entrypoints/builder.py",
        "stop_gis/entrypoints/published.py",
        "stop_gis/entrypoints/public_voting.py",
        "stop_gis/public_app.py",
        "stop_gis/public_voting.py",
        "stop_gis/persistence/store.py",
        "stop_gis/builder/imports.py",
        "stop_gis/builder/labels.py",
        "stop_gis/builder/visuals.py",
        "stop_gis/domain/blind_coding.py",
        "stop_gis/domain/data_quality.py",
        "stop_gis/domain/identifiers.py",
        "stop_gis/domain/shade_dimensions.py",
        "stop_gis/ui/tables.py",
        "stop_gis/ui/taxonomy.py",
        "stop_gis/ui/data_quality.py",
        "stop_gis/deploy/artifacts.py",
        "stop_gis/deploy/bundle.py",
        "stop_gis/deploy/service.py",
        "stop_gis/pages/preview_page.py",
        "stop_gis/pages/data_quality_page.py",
        "stop_gis/pages/voting_page.py",
        "stop_gis/pages/deploy_page.py",
    ]:
        source = Path(filename).read_text(encoding="utf-8")
        compile(source, filename, "exec")


def test_deploy_source_comes_from_public_app_module():
    import stop_gis.builder.app as builder_app

    assert builder_app.published_app_source() == Path("stop_gis/entrypoints/published.py").read_text(
        encoding="utf-8"
    )


def test_tracked_preview_app_matches_published_source():
    assert Path("examples/published-site/app.py").read_text(encoding="utf-8") == Path(
        "stop_gis/entrypoints/published.py"
    ).read_text(encoding="utf-8")

    assert Path("examples/published-site/public_voting.py").read_text(
        encoding="utf-8"
    ) == Path("stop_gis/entrypoints/public_voting.py").read_text(encoding="utf-8")


def test_builder_and_published_runtime_disable_arrow_string_inference():
    import stop_gis.builder.app as builder_app  # noqa: F401 - importing applies the runtime guard

    for filename in ["stop_gis/builder/app.py", "stop_gis/public_app.py"]:
        source = Path(filename).read_text(encoding="utf-8")
        assert "pd.options.future.infer_string = False" in source

    inferred = pd.DataFrame({"value": [""]})
    assert pd.options.future.infer_string is False
    assert inferred["value"].dtype == object


def test_runtime_and_generated_bundle_pin_pandas_below_three():
    requirements = Path("requirements/requirements.txt").read_text(encoding="utf-8")
    bundle_source = Path("stop_gis/deploy/bundle.py").read_text(encoding="utf-8")

    assert "pandas>=2.2,<3" in requirements
    assert "pyarrow>=24,<25" in requirements
    assert '"streamlit>=1.57,<2\\n"' in bundle_source
    assert '"pandas>=2.2,<3\\n"' in bundle_source
    assert '"pyarrow>=24,<25\\n"' in bundle_source


def test_builder_coordinates_deployment_without_embedding_generated_scripts():
    builder_source = Path("stop_gis/builder/app.py").read_text(encoding="utf-8")
    artifact_source = Path("stop_gis/deploy/artifacts.py").read_text(encoding="utf-8")
    powershell_template = Path(
        "stop_gis/deploy/templates/deploy_to_github.ps1"
    ).read_text(encoding="utf-8")

    assert "DeploymentBundleSpec(" in builder_source
    assert "function Commit-And-Push" not in builder_source
    assert '"deploy_to_github.ps1"' in artifact_source
    assert "function Commit-And-Push" in powershell_template


def test_builder_launcher_uses_canonical_application():
    from stop_gis.entrypoints import builder

    assert builder.main.__module__ == "stop_gis.builder.app"


def test_visual_pages_do_not_reference_removed_dense_map_overrides():
    for filename in [
        "stop_gis/pages/visuals_page.py",
        "stop_gis/pages/preview_page.py",
        "stop_gis/public_app.py",
    ]:
        source = Path(filename).read_text(encoding="utf-8")
        assert "DENSE_MAP_THRESHOLD" not in source
        assert "DENSE_MAP_MARKER_SIZE" not in source


def test_ui_smoke_can_disable_expensive_automatic_persistence(monkeypatch):
    import stop_gis.builder.app as builder_app

    monkeypatch.setenv("SHADE_GIS_TEST_DISABLE_AUTO_SAVE", "1")
    monkeypatch.setattr(
        builder_app,
        "st",
        type("FakeStreamlit", (), {"session_state": {"active_project_id": "ui-test"}}),
    )
    monkeypatch.setattr(
        builder_app,
        "save_project_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("auto-save should be skipped")
        ),
    )

    builder_app.save_active_project_to_store()


def test_ui_seed_limit_is_test_only(monkeypatch, taxonomy, project):
    import stop_gis.builder.app as builder_app

    full_seed = builder_app.load_seed_dataset(taxonomy, project)
    monkeypatch.setenv("SHADE_GIS_TEST_MAX_SEED_ROWS", "25")
    monkeypatch.setattr(builder_app, "load_seed_dataset", lambda *args: full_seed)
    captured = {}
    monkeypatch.setattr(
        builder_app,
        "create_project",
        lambda project, taxonomy, methodology, visualization, stops, import_log: (
            captured.update({"stops": stops, "rows": import_log[0]["rows"]})
            or "ui-seed"
        ),
    )

    assert builder_app.create_seed_project() == "ui-seed"
    assert len(captured["stops"]) == 25
    assert captured["rows"] == 25


def test_project_label_progress_preserves_small_nonzero_values():
    import stop_gis.builder.app as builder_app

    percent, label = builder_app.project_label_progress(7, 2_315)

    assert percent == pytest.approx(7 / 2_315 * 100)
    assert label == "0.3%"
    assert builder_app.project_label_progress(1, 2_000)[1] == "<0.1%"
    assert builder_app.project_label_progress(0, 2_315) == (0.0, "0%")
    assert builder_app.project_label_progress(2_315, 2_315) == (100.0, "100%")


def test_summary_metrics_only_render_in_analytics():
    published_source = Path("stop_gis/public_app.py").read_text(encoding="utf-8")
    preview_source = Path("stop_gis/pages/preview_page.py").read_text(encoding="utf-8")

    assert published_source.count("render_metric_cards(df)") == 1
    assert "published_app.render_metric_cards(visible_stops)" not in preview_source


def test_public_taxonomy_table_does_not_expose_sort_order():
    source = Path("stop_gis/public_app.py").read_text(encoding="utf-8")

    assert (
        'coverage_schema_display_table(taxonomy, config.get("shade_coverage_taxonomy"))'
        in source
    )
    assert 'source_schema_display_table(config.get("shade_source_taxonomy"))' in source
    assert 'drop(columns=["sort_order"]' in source


def test_preview_uses_the_shared_stop_and_voting_panel():
    preview_source = Path("stop_gis/pages/preview_page.py").read_text(encoding="utf-8")

    assert "published_app.render_stop_and_voting_panel(" in preview_source


def test_agreement_workflow_is_embedded_in_preview_analytics_not_top_level_navigation():
    builder_source = Path("stop_gis/builder/app.py").read_text(encoding="utf-8")
    preview_source = Path("stop_gis/pages/preview_page.py").read_text(encoding="utf-8")

    assert '("Dataset", "Data")' in builder_source
    assert '("Labeling", "Labels")' in builder_source
    assert '("Build", "Preview")' in builder_source
    assert '("Publish", "Deploy")' in builder_source
    assert '"Dataset": [' in builder_source
    assert '("Quality", "Data Quality")' in builder_source
    assert '("Dataset Review", "Labels")' in builder_source
    assert '("Intercoder Review", "Blind Coding")' in builder_source
    assert '("Community Voting", "Voting")' in builder_source
    assert 'key=f"primary_nav_{label.lower()}"' in builder_source
    assert 'key="header_project"' in builder_source
    assert 'f"{selector_label} ▾"' not in builder_source
    assert "gap: 0.5rem !important" in builder_source
    assert "flex: 0 0 auto !important" in builder_source
    assert 'elif page == "Agreement"' not in builder_source
    assert "render_agreement_analytics_section(" not in preview_source
    assert "include_agreement=True" in preview_source


def test_builder_has_project_home_and_clickable_brand_navigation():
    source = Path("stop_gis/builder/app.py").read_text(encoding="utf-8")

    assert 'st.title("Stop-GIS Projects")' not in source
    assert 'st.button("Stop-GIS", key="nav_home", on_click=request_main_menu)' in source
    assert '@st.dialog("Open project?"' not in source
    assert (
        '@st.dialog("Project settings", on_dismiss=clear_pending_project_settings)'
        in source
    )
    assert (
        '@st.dialog("Delete project?", on_dismiss=clear_pending_project_delete)'
        in source
    )
    assert '@st.dialog("Return to main menu?"' not in source
    assert 'def request_main_menu() -> None:\n    set_page("Home")' in source
    assert 'with st.container(key="home_page")' in source
    assert 'f"Open project: {name}"' in source
    assert 'key=f"project_settings_{project_id}"' in source
    assert "project-card-badge" not in source
    assert "it does not move the map, filter data, or set a boundary" in source
    assert "publishing the website is still a separate step" in source
    assert '"Delete permanently"' in source
    assert "disabled=confirmation != project_name" in source
    assert "max-width: 1080px" in source
    assert 'class="home-summary"' not in source
    assert 'div[class*="st-key-project_card_"]:hover' in source
    assert (
        'div[class*="st-key-project_card_"] div[class*="st-key-home_open_"] {' in source
    )
    assert ".st-key-nav_home button:hover" in source
    assert ".st-key-nav_home button:active" in source
    assert ".st-key-nav_home button:focus-visible" in source


def test_labeling_workflow_uses_action_oriented_navigation():
    source = Path("stop_gis/pages/labels_page.py").read_text(encoding="utf-8")

    assert 'st.title("Dataset Review")' in source
    assert '"+ Submit Label"' in source
    assert '"Review Queue"' in source
    assert '"Audit History"' in source
    assert "Create labels, resolve conflicts" in source
    assert 'st.subheader("Label Review Queue")' in source
    assert (
        "Stops awaiting moderator review, conflict resolution, or verification."
        in source
    )
    assert 'st.subheader("Summary")' not in source
    assert '"Admin Review Queue"' not in source
    assert '"+ Add Administrative Label"' not in source
    assert '"Submit raw label"' not in source


def test_blind_coding_uses_research_workflow_hierarchy():
    source = Path("stop_gis/pages/blind_coding_page.py").read_text(encoding="utf-8")

    assert 'st.title("Intercoder Review")' in source
    assert 'st.subheader("Study Setup")' in source
    assert 'st.subheader("Review Materials")' in source
    assert 'st.subheader("Study Progress")' in source
    assert '"Reviews per item"' in source
    assert '"Agreement threshold"' in source
    assert "Items below this agreement level are flagged for adjudication." in source
    assert '"Add Review Image"' in source
    assert '"Start Intercoder Review"' in source
    assert 'st.expander("Advanced versioning", expanded=False)' in source
    assert 'st.expander("Admin preview", expanded=False)' in source
    assert '"Workspace role"' not in source
    assert '"Open blind coding"' not in source
    assert '@st.dialog("Confirm workflow change"' in source
    assert '"This phase transition cannot be undone."' in source
    assert "advance_blind_phase(project_id)" in source


def test_voting_groups_configuration_and_hides_deployment_details():
    source = Path("stop_gis/pages/voting_page.py").read_text(encoding="utf-8")

    assert 'st.title("Community Voting")' in source
    assert 'st.subheader("Configuration")' in source
    assert 'st.subheader("Live Preview")' in source
    for group in [
        "Visitor Experience",
        "Voting Options",
        "Abuse Prevention",
        "Result Display",
    ]:
        assert f'st.expander("{group}"' in source
    assert 'if not voting["enabled"]:' in source
    assert "return voting" in source
    assert 'if voting["enabled"]:' in source
    assert 'st.markdown("#### Persistent voting storage")' in source
    assert 'st.button("Open deployment setup"' in source
    assert "SHADE_GIS_VOTE_DATABASE_URL" not in source
    assert "render_voting_panel(" in source
    assert "preview=True" in source
    assert 'st.subheader("Deployment Storage")' not in source


def test_preview_configuration_pages_do_not_duplicate_full_public_renderers():
    visuals = Path("stop_gis/pages/visuals_page.py").read_text(encoding="utf-8")
    docs = Path("stop_gis/pages/docs_page.py").read_text(encoding="utf-8")

    assert '"Open full preview →"' in visuals
    assert 'st.subheader("Custom Chart Preview")' not in visuals
    assert 'st.subheader("Data Table Preview")' not in visuals
    assert 'st.subheader("Available Fields")' not in visuals
    assert "render_builder_about_page" not in docs
    assert "authoritative public rendering" in docs


def test_public_voting_requires_an_explicit_complete_response():
    source = Path("stop_gis/public_voting.py").read_text(encoding="utf-8")
    default_assignment = source.split("default_index =", 1)[1].split(
        "st.markdown(", 1
    )[0]
    assert "0 if preview else None" in default_assignment
    assert "coverage_selected = selected_status in options" in source
    assert "or not coverage_selected or sources_required" in source
    assert "Select at least one shade source to submit this response." in source


def test_public_filters_use_one_disclosure_instead_of_nested_expanders():
    source = Path("stop_gis/public_app.py").read_text(encoding="utf-8")
    assert 'st.expander("Map and analytics filters", expanded=False)' in source
    assert 'st.expander("Map filters"' not in source
    assert '"Clear filters"' in source
    assert '"Clear filters and show stops"' in source


def test_workspace_ux_safeguards_are_present():
    builder = Path("stop_gis/builder/app.py").read_text(encoding="utf-8")
    data_page = Path("stop_gis/pages/data_page.py").read_text(encoding="utf-8")
    labels_page = Path("stop_gis/pages/labels_page.py").read_text(encoding="utf-8")
    taxonomy = Path("stop_gis/ui/taxonomy.py").read_text(encoding="utf-8")
    visuals = Path("stop_gis/pages/visuals_page.py").read_text(encoding="utf-8")

    assert '"Publication intent"' in data_page
    assert "save automatically to the active project" in data_page
    assert '"Save now"' not in data_page
    assert 'type="primary" if current == view else "secondary"' in labels_page
    assert 'key="label_workflow_navigation"' in labels_page
    assert "render_taxonomy_editor" in taxonomy
    assert '"Disable" if mode["enabled"] else "Enable"' in taxonomy
    assert '":blue-badge[Custom]"' in taxonomy
    assert '"Schema details"' in taxonomy
    assert 'role="status" aria-live="polite"' in builder
    assert '@st.dialog("Remove GIS overlay?"' in visuals
    assert "min-height: 24.5rem" in builder
    assert "\n            height: 24.5rem;" not in builder
    assert "max-width: 760px" in builder


def test_page_modules_use_explicit_dependencies_and_shared_components():
    page_sources = [
        path.read_text(encoding="utf-8")
        for path in Path("stop_gis/pages").glob("*.py")
    ]

    assert all("from stop_gis.builder.app import *" not in source for source in page_sources)
    assert "stop_gis.ui.data_quality" in Path(
        "stop_gis/pages/data_quality_page.py"
    ).read_text(encoding="utf-8")
    assert "stop_gis.ui.taxonomy" in Path(
        "stop_gis/pages/taxonomy_page.py"
    ).read_text(encoding="utf-8")


def test_data_page_uses_progress_dashboard_and_collapsed_dataset_preview():
    source = Path("stop_gis/pages/data_page.py").read_text(encoding="utf-8")

    assert 'st.subheader("Dataset Status")' in source
    assert '"Open Dataset Review →"' in source
    assert '"Show stops"' not in source
    assert '"Work Queue"' not in source
    assert 'st.expander("Dataset Preview", expanded=False)' in source
    assert "render_dataframe_table(visible_stops)" in source
    assert "st.dataframe(" not in source
    assert 'st.subheader("Dataset Health")' not in source
    assert "render_data_quality_dashboard" not in source


def test_data_quality_has_a_dedicated_data_menu_page():
    source = Path("stop_gis/pages/data_quality_page.py").read_text(encoding="utf-8")

    assert 'st.title("Data Quality")' in source
    assert "render_data_quality_dashboard(" in source
    assert "show_heading=False" in source


def test_manual_entry_form_does_not_use_arrow_backed_dataframe_widget():
    source = Path("stop_gis/pages/data_page.py").read_text(encoding="utf-8")
    manual_entry_source = source.split("with manual_tab:", 1)[1].split(
        "source_cols = st.columns", 1
    )[0]

    assert 'st.form("manual_entry_form", clear_on_submit=True)' in source
    assert "st.data_editor(" not in manual_entry_source
    assert 'with st.container(key="taxonomy_workspace")' not in source


def test_taxonomy_has_a_dedicated_data_menu_page():
    source = Path("stop_gis/pages/taxonomy_page.py").read_text(encoding="utf-8")
    components = Path("stop_gis/ui/taxonomy.py").read_text(encoding="utf-8")

    assert 'st.title("Taxonomy")' in source
    assert "render_taxonomy_editor(" in source
    assert 'with st.container(key="taxonomy_workspace")' in source
    assert '"Search dimensions"' in components
    assert '"Search terminology"' in components
    assert '"+ Add dimension"' in components
    assert '["Dimensions", "Terminology"]' in components
    assert '"Disable" if mode["enabled"] else "Enable"' in components
    assert "_render_group_header" in components
    assert "taxonomy-value-row" in components
    assert '"Schema details"' in components
    assert "render_assessment_mode_builder" not in source
    assert "st.data_editor(" not in components
    assert "max-width: 960px" in source


def test_preview_exports_use_catalog_and_provenance_sections():
    source = Path("stop_gis/pages/preview_page.py").read_text(encoding="utf-8")

    assert "published_app.render_export_files(" in source
    assert "published_app.render_dataset_provenance(" in source
    assert 'st.dataframe(pd.DataFrame(st.session_state["import_log"])' not in source
    assert '"Download stops CSV"' not in source
