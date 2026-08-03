from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


def test_core_modules_compile_without_bytecode_writes():
    for filename in [
        "builder_app.py",
        "platform_store.py",
        "app.py",
        "published_app.py",
        "public_voting.py",
        "shade_gis/shade_dimensions.py",
        "shade_gis/deploy/artifacts.py",
        "shade_gis/deploy/bundle.py",
        "shade_gis/pages/preview_page.py",
        "shade_gis/pages/data_quality_page.py",
        "shade_gis/pages/agreement_page.py",
        "shade_gis/pages/voting_page.py",
        "shade_gis/pages/deploy_page.py",
    ]:
        source = Path(filename).read_text(encoding="utf-8")
        compile(source, filename, "exec")


def test_deploy_source_comes_from_public_app_module():
    import builder_app

    assert builder_app.published_app_source() == Path("published_app.py").read_text(encoding="utf-8")


def test_tracked_preview_app_matches_published_source():
    assert Path("preview_app/app.py").read_text(encoding="utf-8") == Path("published_app.py").read_text(
        encoding="utf-8"
    )


def test_builder_and_published_runtime_disable_arrow_string_inference():
    import builder_app  # noqa: F401 - importing applies the runtime guard

    for filename in ["builder_app.py", "published_app.py"]:
        source = Path(filename).read_text(encoding="utf-8")
        assert "pd.options.future.infer_string = False" in source

    inferred = pd.DataFrame({"value": [""]})
    assert pd.options.future.infer_string is False
    assert inferred["value"].dtype == object


def test_runtime_and_generated_bundle_pin_pandas_below_three():
    requirements = Path("requirements/requirements.txt").read_text(encoding="utf-8")
    bundle_source = Path("shade_gis/deploy/bundle.py").read_text(encoding="utf-8")

    assert "pandas>=2.2,<3" in requirements
    assert "pyarrow>=24,<25" in requirements
    assert '"streamlit>=1.57,<2\\n"' in bundle_source
    assert '"pandas>=2.2,<3\\n"' in bundle_source
    assert '"pyarrow>=24,<25\\n"' in bundle_source


def test_builder_coordinates_deployment_without_embedding_generated_scripts():
    builder_source = Path("builder_app.py").read_text(encoding="utf-8")
    artifact_source = Path("shade_gis/deploy/artifacts.py").read_text(encoding="utf-8")
    powershell_template = Path(
        "shade_gis/deploy/templates/deploy_to_github.ps1"
    ).read_text(encoding="utf-8")

    assert "DeploymentBundleSpec(" in builder_source
    assert "function Commit-And-Push" not in builder_source
    assert '"deploy_to_github.ps1"' in artifact_source
    assert "function Commit-And-Push" in powershell_template


def test_app_py_is_builder_entrypoint():
    import app

    assert app.main.__module__ == "builder_app"


def test_ui_smoke_can_disable_expensive_automatic_persistence(monkeypatch):
    import builder_app

    monkeypatch.setenv("SHADE_GIS_TEST_DISABLE_AUTO_SAVE", "1")
    monkeypatch.setattr(builder_app, "st", type("FakeStreamlit", (), {"session_state": {"active_project_id": "ui-test"}}))
    monkeypatch.setattr(
        builder_app,
        "save_project_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("auto-save should be skipped")),
    )

    builder_app.save_active_project_to_store()


def test_ui_seed_limit_is_test_only(monkeypatch, taxonomy, project):
    import builder_app

    full_seed = builder_app.load_seed_dataset(taxonomy, project)
    monkeypatch.setenv("SHADE_GIS_TEST_MAX_SEED_ROWS", "25")
    monkeypatch.setattr(builder_app, "load_seed_dataset", lambda *args: full_seed)
    captured = {}
    monkeypatch.setattr(
        builder_app,
        "create_project",
        lambda project, taxonomy, methodology, visualization, stops, import_log: captured.update(
            {"stops": stops, "rows": import_log[0]["rows"]}
        )
        or "ui-seed",
    )

    assert builder_app.create_seed_project() == "ui-seed"
    assert len(captured["stops"]) == 25
    assert captured["rows"] == 25


def test_project_label_progress_preserves_small_nonzero_values():
    import builder_app

    percent, label = builder_app.project_label_progress(7, 2_315)

    assert percent == pytest.approx(7 / 2_315 * 100)
    assert label == "0.3%"
    assert builder_app.project_label_progress(1, 2_000)[1] == "<0.1%"
    assert builder_app.project_label_progress(0, 2_315) == (0.0, "0%")
    assert builder_app.project_label_progress(2_315, 2_315) == (100.0, "100%")


def test_summary_metrics_only_render_in_analytics():
    published_source = Path("published_app.py").read_text(encoding="utf-8")
    preview_source = Path("shade_gis/pages/preview_page.py").read_text(encoding="utf-8")

    assert published_source.count("render_metric_cards(df)") == 1
    assert "published_app.render_metric_cards(visible_stops)" not in preview_source


def test_public_taxonomy_table_does_not_expose_sort_order():
    source = Path("published_app.py").read_text(encoding="utf-8")

    assert 'coverage_schema_display_table(taxonomy, config.get("shade_coverage_taxonomy"))' in source
    assert 'source_schema_display_table(config.get("shade_source_taxonomy"))' in source
    assert 'drop(columns=["sort_order"]' in source


def test_builder_docs_taxonomy_table_does_not_expose_sort_order():
    source = Path("builder_about_page.py").read_text(encoding="utf-8")

    assert "st.dataframe(builder_taxonomy_display_table(taxonomy)" in source
    assert '["sort_order", "name", "description", "color"]' not in source


def test_preview_uses_the_shared_stop_and_voting_panel():
    preview_source = Path("shade_gis/pages/preview_page.py").read_text(encoding="utf-8")

    assert "published_app.render_stop_and_voting_panel(" in preview_source


def test_agreement_workflow_is_embedded_in_preview_analytics_not_top_level_navigation():
    builder_source = Path("builder_app.py").read_text(encoding="utf-8")
    preview_source = Path("shade_gis/pages/preview_page.py").read_text(encoding="utf-8")

    assert '("Dataset", "Data")' in builder_source
    assert '("Labelling", "Labels")' in builder_source
    assert '("Preview", "Preview")' in builder_source
    assert '("Export", "Deploy")' in builder_source
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
    assert 'agreement_enabled = "Agreement metrics" in selected_sections' in preview_source
    assert "render_agreement_analytics_section(" in preview_source
    assert "include_agreement=False" in preview_source


def test_builder_has_project_home_and_clickable_brand_navigation():
    source = Path("builder_app.py").read_text(encoding="utf-8")

    assert 'st.title("Shade-GIS Projects")' not in source
    assert 'st.button("Shade-GIS", key="nav_home", on_click=request_main_menu)' in source
    assert '@st.dialog("Open project?", on_dismiss=clear_pending_project_open)' in source
    assert '@st.dialog("Project settings", on_dismiss=clear_pending_project_settings)' in source
    assert '@st.dialog("Delete project?", on_dismiss=clear_pending_project_delete)' in source
    assert '@st.dialog("Return to main menu?", on_dismiss=clear_pending_main_menu)' in source
    assert 'with st.container(key="home_page")' in source
    assert 'f"Open project: {name}"' in source
    assert 'key=f"project_settings_{project_id}"' in source
    assert "it does not move the map, filter data, or set a boundary" in source
    assert "publishing the website is still a separate step" in source
    assert '"Delete permanently"' in source
    assert 'disabled=confirmation != project_name' in source
    assert "max-width: 1080px" in source
    assert 'class="home-summary"' not in source
    assert 'div[class*="st-key-project_card_"]:hover' in source
    assert 'div[class*="st-key-project_card_"] div[class*="st-key-home_open_"] {' in source
    assert ".st-key-nav_home button:hover" in source
    assert ".st-key-nav_home button:active" in source
    assert ".st-key-nav_home button:focus-visible" in source


def test_labelling_workflow_uses_action_oriented_navigation():
    source = Path("shade_gis/pages/labels_page.py").read_text(encoding="utf-8")

    assert 'st.title("Dataset Review")' in source
    assert '"+ Add Administrative Label"' in source
    assert '"Review Queue"' in source
    assert '"Audit History"' in source
    assert "Review submitted labels, resolve conflicts" in source
    assert 'st.subheader("Label Review Queue")' in source
    assert "Stops awaiting moderator review, conflict resolution, or verification." in source
    assert 'st.subheader("Summary")' not in source
    assert '"Admin Review Queue"' not in source
    assert '"+ Submit Label"' not in source
    assert '"Submit raw label"' not in source


def test_independent_review_uses_research_workflow_hierarchy():
    source = Path("shade_gis/pages/blind_coding_page.py").read_text(encoding="utf-8")

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


def test_consensus_groups_configuration_and_hides_deployment_details():
    source = Path("shade_gis/pages/voting_page.py").read_text(encoding="utf-8")

    assert 'st.title("Community Voting")' in source
    assert 'st.subheader("Configuration")' in source
    assert 'st.subheader("Live Preview")' in source
    for group in ["Visitor Experience", "Voting Options", "Abuse Prevention", "Result Display"]:
        assert f'st.expander("{group}"' in source
    assert 'st.expander("Advanced Deployment", expanded=False)' in source
    assert "SHADE_GIS_VOTE_DATABASE_URL" in source
    assert 'st.subheader("Deployment Storage")' not in source


def test_data_page_uses_progress_dashboard_and_collapsed_dataset_preview():
    source = Path("shade_gis/pages/data_page.py").read_text(encoding="utf-8")

    assert 'st.subheader("Dataset Status")' in source
    assert 'st.markdown("#### Work Queue")' in source
    assert "with st.expander(queue_label, expanded=False)" in source
    assert '"Show stops"' in source
    assert '"Open labeling workspace →"' in source
    assert 'st.expander("Dataset Preview", expanded=False)' in source
    assert "render_dataframe_table(visible_stops)" in source
    assert "st.dataframe(" not in source
    assert 'st.subheader("Dataset Health")' not in source
    assert 'render_data_quality_dashboard(st.session_state["stops"]' not in source


def test_data_quality_has_a_dedicated_data_menu_page():
    source = Path("shade_gis/pages/data_quality_page.py").read_text(encoding="utf-8")

    assert 'st.title("Data Quality")' in source
    assert "render_data_quality_dashboard(" in source
    assert "show_heading=False" in source


def test_manual_entry_form_does_not_use_arrow_backed_dataframe_widget():
    source = Path("shade_gis/pages/data_page.py").read_text(encoding="utf-8")
    manual_entry_source = source.split("with manual_tab:", 1)[1].split("source_cols = st.columns", 1)[0]

    assert 'st.form("manual_entry_form", clear_on_submit=True)' in source
    assert "st.data_editor(" not in manual_entry_source
    assert 'with st.container(key="taxonomy_workspace")' not in source


def test_taxonomy_has_a_dedicated_data_menu_page():
    source = Path("shade_gis/pages/taxonomy_page.py").read_text(encoding="utf-8")

    assert 'st.title("Taxonomy")' in source
    assert "terminology_editing = render_taxonomy_section_header(" in source
    assert "render_terminology_editor(methodology)" in source
    assert "render_shade_source_taxonomy_editor(methodology)" in source
    assert "render_shade_coverage_taxonomy_editor(methodology, taxonomy)" in source
    assert '"Reset definitions"' in Path("shade_gis/pages/data_page.py").read_text(encoding="utf-8")
    assert 'with st.container(key="terminology_table")' in source
    assert 'with st.container(key="taxonomy_workspace")' in source
    assert 'with st.container(key="taxonomy_card_terminology")' in source
    assert 'with st.container(key="taxonomy_card_source")' in source
    assert 'with st.container(key="taxonomy_card_coverage")' in source
    assert "max-width: 1120px" in source
    assert "table-layout: fixed" in source


def test_preview_exports_use_catalog_and_provenance_sections():
    source = Path("shade_gis/pages/preview_page.py").read_text(encoding="utf-8")

    assert "published_app.render_export_files(" in source
    assert "published_app.render_dataset_provenance(" in source
    assert 'st.dataframe(pd.DataFrame(st.session_state["import_log"])' not in source
    assert '"Download stops CSV"' not in source
