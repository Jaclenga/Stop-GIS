from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pytest
import tempfile


pytestmark = pytest.mark.ui


@dataclass
class StreamlitServer:
    url: str
    process: subprocess.Popen[str]
    output: deque[str]

    def log_tail(self) -> str:
        return "".join(self.output) or "(no server output captured)"


def confirm_seed_project_open(page) -> None:
    project_card = page.locator('div[class*="st-key-project_card_"]').first
    project_card.click(position={"x": 40, "y": 40}, timeout=30_000)
    page.get_by_role("heading", name="Project Data", exact=True).wait_for(
        timeout=30_000
    )


def confirm_main_menu_return(page) -> None:
    page.get_by_role("button", name="Stop-GIS", exact=True).click(timeout=30_000)
    page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
        timeout=30_000
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_streamlit_health(port: int, timeout_seconds: int = 45) -> None:
    deadline = time.time() + timeout_seconds
    url = f"http://127.0.0.1:{port}/_stcore/health"
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as error:
            last_error = error
        time.sleep(0.5)
    raise RuntimeError(f"Streamlit health check did not pass at {url}: {last_error}")


def assert_streamlit_server_alive(streamlit_server: StreamlitServer) -> None:
    return_code = streamlit_server.process.poll()
    if return_code is not None:
        raise RuntimeError(
            f"Streamlit server exited with code {return_code}.\n"
            f"Server log tail:\n{streamlit_server.log_tail()}"
        )


def wait_for_streamlit_idle(
    playwright_api,
    page,
    streamlit_server: StreamlitServer,
    timeout_ms: int = 60_000,
) -> None:
    # Streamlit delays its running indicator by ~500 ms. Waiting past that
    # threshold distinguishes a rendered page from a fully completed script run.
    #
    # Newer Streamlit versions may keep the connection-status element mounted
    # in the DOM and toggle its visibility / contents during reconnects. Tests
    # should therefore check that the running icon is gone and that there is
    # no visible "Connecting" text or active "Connection error" dialog.
    page.wait_for_timeout(700)
    assert_streamlit_server_alive(streamlit_server)
    running_icon = page.get_by_test_id("stStatusWidgetRunningIcon")
    deadline = time.monotonic() + (timeout_ms / 1000)
    while running_icon.count() > 0:
        assert_streamlit_server_alive(streamlit_server)
        if time.monotonic() >= deadline:
            playwright_api.expect(running_icon).to_have_count(0, timeout=1)
        page.wait_for_timeout(250)
    assert_streamlit_server_alive(streamlit_server)

    # The connection status node may remain mounted in newer Streamlit
    # versions. If it exists, assert it is not visible instead of asserting
    # its absence from the DOM.
    connection_status = page.get_by_test_id("stConnectionStatus")
    try:
        if connection_status.count() > 0:
            playwright_api.expect(connection_status).not_to_be_visible(
                timeout=timeout_ms
            )
    except Exception:
        # If the locator operations fail for some reason, fall back to the
        # more general check for visible "Connecting" text.
        try:
            playwright_api.expect(page.get_by_text("Connecting")).to_have_count(
                0, timeout=timeout_ms
            )
        except Exception:
            pass
    assert_streamlit_server_alive(streamlit_server)


def reconnect_streamlit_page(
    page, streamlit_server: StreamlitServer, attempts: int = 3
) -> None:
    """Reconnect after a wedged frontend, retrying transient server unavailability."""
    last_error: Exception | None = None
    port = int(streamlit_server.url.rsplit(":", 1)[1])
    for _ in range(attempts):
        if streamlit_server.process.poll() is not None:
            raise RuntimeError(
                f"Streamlit server exited with code {streamlit_server.process.returncode}.\n"
                f"Server log tail:\n{streamlit_server.log_tail()}"
            )
        try:
            wait_for_streamlit_health(port, timeout_seconds=15)
            page.goto(
                streamlit_server.url, wait_until="domcontentloaded", timeout=30_000
            )
            return
        except Exception as error:
            last_error = error
            page.wait_for_timeout(1000)
    raise RuntimeError(
        f"Could not reconnect to the Streamlit test server: {last_error}"
    ) from last_error


def choose_streamlit_selectbox_option(
    playwright_api,
    page,
    selectbox,
    option_name: str,
    attempts: int = 2,
) -> None:
    """Choose an option even if a late Streamlit rerun closes the popup once."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        combobox = selectbox.get_by_role("combobox")
        playwright_api.expect(combobox).to_be_enabled(timeout=30_000)
        combobox.click(timeout=30_000)
        option = page.get_by_role("option", name=option_name, exact=True)
        try:
            option.click(timeout=15_000)
            return
        except Exception as error:
            last_error = error
            if attempt + 1 < attempts:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)

    assert last_error is not None
    raise last_error


def navigate_workspace_page(page, page_name: str, heading: str) -> None:
    """Navigate through the task tab and optional second-level tab."""
    sections = {
        "Dataset Review": ("Labeling", "Dataset Review", "Dataset Review"),
        "Intercoder Review": ("Labeling", "Dataset Review", "Dataset Review"),
        "Community Voting": ("Labeling", "Dataset Review", "Dataset Review"),
        "Visuals": ("Build", "Preview", "Tampa Bus Stop Infrastructure Demo"),
        "Docs": ("Build", "Preview", "Tampa Bus Stop Infrastructure Demo"),
        "Preview": ("Build", "Preview", "Tampa Bus Stop Infrastructure Demo"),
        "Data Quality": ("Dataset", "Data", "Project Data"),
        "Deploy": ("Publish", "Deploy", "Publish website"),
    }
    section, default_page, default_heading = sections[page_name]
    page.locator(f".st-key-primary_nav_{section.lower()}").get_by_role("button").click(
        timeout=30_000
    )
    page.get_by_role("heading", name=default_heading, exact=True).wait_for(
        timeout=30_000
    )
    if page_name != default_page:
        secondary_label = "Quality" if page_name == "Data Quality" else page_name
        page.get_by_role("button", name=secondary_label, exact=True).click(
            timeout=30_000
        )
    page.get_by_role("heading", name=heading, exact=True).wait_for(timeout=30_000)


@pytest.fixture
def playwright_api():
    return pytest.importorskip("playwright.sync_api")


@pytest.fixture
def streamlit_server(playwright_api):
    port = free_port()
    temp_root = (
        Path(os.environ.get("TEMP", ".")) / "shade_gis_ui_tests" / uuid.uuid4().hex
    )
    temp_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONFAULTHANDLER": "1",
            "SHADE_GIS_DB_PATH": str(temp_root / "shade-gis-ui.sqlite3"),
            "SHADE_GIS_VOTE_DB_PATH": str(temp_root / "shade-gis-votes-ui.sqlite3"),
            "SHADE_GIS_TEST_DISABLE_AUTO_SAVE": "1",
            "SHADE_GIS_TEST_MAX_SEED_ROWS": "100",
            "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
            "STREAMLIT_SERVER_HEADLESS": "true",
        }
    )
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "app.py",
        "--server.port",
        str(port),
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--server.fileWatcherType",
        "none",
        "--runner.fastReruns",
        "false",
        "--browser.gatherUsageStats",
        "false",
    ]
    process: subprocess.Popen[str] = subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output: deque[str] = deque(maxlen=500)

    def drain_server_output() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            output.append(line)

    output_thread = threading.Thread(target=drain_server_output, daemon=True)
    output_thread.start()
    try:
        wait_for_streamlit_health(port)
        # Ensure the root page is responding with HTTP 200. The Streamlit app
        # frontend is rendered client-side via JS and WebSocket; a successful
        # HTTP response is sufficient here. The test itself uses Playwright to
        # wait for rendered content.
        root_url = f"http://127.0.0.1:{port}/"
        root_deadline = time.time() + 45
        last_error: Exception | None = None
        while time.time() < root_deadline:
            try:
                with urllib.request.urlopen(root_url, timeout=2) as response:
                    if getattr(response, "status", None) == 200:
                        break
            except Exception as error:
                last_error = error
            time.sleep(0.5)
        else:
            raise RuntimeError(
                f"Streamlit root page did not become available: {last_error}"
            )
        yield StreamlitServer(f"http://127.0.0.1:{port}", process, output)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        output_thread.join(timeout=5)
        shutil.rmtree(temp_root, ignore_errors=True)


def test_builder_header_home_and_grouped_menus(
    playwright_api, streamlit_server: StreamlitServer
):
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            page.goto(streamlit_server.url, wait_until="domcontentloaded")
            page.get_by_role("button", name="Stop-GIS", exact=True).wait_for(
                timeout=30_000
            )
            page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
                timeout=30_000
            )
            playwright_api.expect(
                page.get_by_role("button", name="Dataset", exact=True)
            ).to_have_count(0)
            playwright_api.expect(
                page.get_by_role("button", name="Build", exact=True)
            ).to_have_count(0)
            project_card = page.locator('div[class*="st-key-project_card_"]').first
            project_card.hover()
            playwright_api.expect(project_card).to_have_css(
                "border-color", "rgb(74, 222, 128)"
            )

            page.get_by_role("button", name="Stop-GIS", exact=True).click(
                timeout=30_000
            )
            playwright_api.expect(page.get_by_role("dialog")).to_have_count(0)
            page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
                timeout=30_000
            )

            confirm_seed_project_open(page)
            page.get_by_role("heading", name="Project Data", exact=True).wait_for(
                timeout=30_000
            )
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)
            manual_entry_tab = page.get_by_role("tab", name="Manual Entry", exact=True)
            manual_entry_tab.click()
            playwright_api.expect(
                page.get_by_role("button", name="Add entry", exact=True)
            ).to_be_visible(timeout=30_000)
            playwright_api.expect(
                page.get_by_role("button", name="Use manual entries", exact=True)
            ).to_be_disabled(timeout=30_000)
            for tab_name in ["Dataset", "Labeling", "Build", "Publish"]:
                playwright_api.expect(
                    page.get_by_role("button", name=tab_name, exact=True)
                ).to_be_visible(timeout=30_000)
            playwright_api.expect(
                page.get_by_role("button", name="Tampa Bus Stop Infras...", exact=True)
            ).to_be_visible(timeout=30_000)

            page.get_by_role("button", name="Labeling", exact=True).click(
                timeout=30_000
            )
            page.get_by_role("heading", name="Dataset Review", exact=True).wait_for(
                timeout=30_000
            )

            page.get_by_role("button", name="Dataset", exact=True).click(timeout=30_000)
            page.get_by_role("heading", name="Project Data", exact=True).wait_for(
                timeout=30_000
            )

            confirm_main_menu_return(page)
            page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
                timeout=30_000
            )
            confirm_seed_project_open(page)
            navigate_workspace_page(page, "Docs", "Project Documentation")

            confirm_main_menu_return(page)
            page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
                timeout=30_000
            )
            playwright_api.expect(
                page.get_by_role("button", name="Dataset", exact=True)
            ).to_have_count(0)
            playwright_api.expect(
                page.get_by_role("button", name="Build", exact=True)
            ).to_have_count(0)
        finally:
            browser.close()

def test_project_settings_can_edit_and_delete_a_project(
    playwright_api, streamlit_server: StreamlitServer
):
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            page.goto(streamlit_server.url, wait_until="domcontentloaded")
            page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
                timeout=30_000
            )

            page.get_by_role("button", name="+ New Project", exact=True).click(
                timeout=30_000
            )
            page.get_by_label("Project name", exact=True).fill("Disposable project")
            page.get_by_role("button", name="Create project", exact=True).click(
                timeout=30_000
            )
            page.get_by_role("heading", name="Project Data", exact=True).wait_for(
                timeout=30_000
            )

            page.get_by_role("button", name="Stop-GIS", exact=True).click(
                timeout=30_000
            )
            page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
                timeout=30_000
            )

            project_card = page.locator('div[class*="st-key-project_card_"]').filter(
                has_text="Disposable project"
            )
            project_card.get_by_role("button", name="⋯", exact=True).click(
                timeout=30_000
            )

            settings_dialog = page.get_by_role("dialog")
            settings_dialog.wait_for(timeout=30_000)
            playwright_api.expect(settings_dialog).to_contain_text("Project settings")
            settings_dialog.get_by_label("Project name", exact=True).fill(
                "Disposable renamed"
            )
            settings_dialog.get_by_label("Agency or organization", exact=True).fill(
                "Test Agency"
            )
            settings_dialog.get_by_label("Location", exact=True).fill("Test Region")
            settings_dialog.get_by_role(
                "button", name="Save changes", exact=True
            ).click(timeout=30_000)
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)

            renamed_card = page.locator('div[class*="st-key-project_card_"]').filter(
                has_text="Disposable renamed"
            )
            playwright_api.expect(renamed_card).to_be_visible(timeout=30_000)
            playwright_api.expect(renamed_card).to_contain_text("Test Agency")
            playwright_api.expect(renamed_card).to_contain_text("Test Region")

            renamed_card.get_by_role("button", name="⋯", exact=True).click(
                timeout=30_000
            )
            settings_dialog = page.get_by_role("dialog")
            settings_dialog.get_by_role(
                "button", name="Delete project", exact=True
            ).click(timeout=30_000)

            delete_dialog = page.get_by_role("dialog")
            delete_dialog.wait_for(timeout=30_000)
            playwright_api.expect(delete_dialog).to_contain_text("Delete project?")
            delete_button = delete_dialog.get_by_role(
                "button", name="Delete permanently", exact=True
            )
            playwright_api.expect(delete_button).to_be_disabled()
            confirmation_input = delete_dialog.get_by_label(
                'Type "Disposable renamed" to confirm', exact=True
            )
            confirmation_input.fill("Disposable renamed")
            confirmation_input.press("Tab")
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)
            delete_dialog = page.get_by_role("dialog")
            delete_button = delete_dialog.get_by_role(
                "button", name="Delete permanently", exact=True
            )
            playwright_api.expect(delete_button).to_be_enabled(timeout=30_000)
            delete_button.click(timeout=30_000)

            page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
                timeout=30_000
            )
            playwright_api.expect(
                page.get_by_role("heading", name="Disposable renamed", exact=True)
            ).to_have_count(0)
            playwright_api.expect(
                page.get_by_text("1 project", exact=True)
            ).to_be_visible()

            remaining_card = page.locator('div[class*="st-key-project_card_"]').first
            remaining_card.get_by_role("button", name="⋯", exact=True).click(
                timeout=30_000
            )
            settings_dialog = page.get_by_role("dialog")
            settings_dialog.get_by_role(
                "button", name="Delete project", exact=True
            ).click(timeout=30_000)
            delete_dialog = page.get_by_role("dialog")
            last_confirmation = delete_dialog.get_by_label(
                'Type "Tampa Bus Stop Infrastructure Demo" to confirm', exact=True
            )
            last_confirmation.fill("Tampa Bus Stop Infrastructure Demo")
            last_confirmation.press("Tab")
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)
            page.get_by_role("dialog").get_by_role(
                "button", name="Delete permanently", exact=True
            ).click(timeout=30_000)

            page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
                timeout=30_000
            )
            playwright_api.expect(
                page.get_by_text("0 projects", exact=True)
            ).to_be_visible()
            playwright_api.expect(
                page.get_by_text(
                    "No saved projects yet. Create your first project to get started.",
                    exact=True,
                )
            ).to_be_visible()
            playwright_api.expect(
                page.locator('div[class*="st-key-project_card_"]')
            ).to_have_count(0)
        finally:
            browser.close()


def test_builder_navigation_pages_render(
    playwright_api, streamlit_server: StreamlitServer
):
    expected_pages = {
        "Dataset Review": "Dataset Review",
        "Intercoder Review": "Intercoder Review",
        "Visuals": "Metrics And Visualizations",
        "Community Voting": "Community Voting",
        "Docs": "Project Documentation",
        "Preview": "Tampa Bus Stop Infrastructure Demo",
        "Deploy": "Publish website",
    }
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        chart_warnings = []
        chart_events: list[tuple[str, str]] = []
        current_surface = {"name": "Data"}
        # Capture all console messages, page errors, failed requests, and
        # websocket events for later debugging in CI.
        page.on(
            "console", lambda message: chart_events.append((message.type, message.text))
        )
        page.on(
            "pageerror", lambda error: chart_events.append(("pageerror", str(error)))
        )
        # Record failed network requests
        page.on(
            "requestfailed",
            lambda request: chart_events.append(("requestfailed", request.url)),
        )

        # Record websocket connections and closures (Playwright exposes a WebSocket object)
        def _on_ws(ws):
            try:
                chart_events.append(("websocket", getattr(ws, "url", "")))
                ws.on(
                    "close",
                    lambda _: chart_events.append(
                        ("websocket_closed", getattr(ws, "url", ""))
                    ),
                )
            except Exception:
                pass

        page.on("websocket", _on_ws)
        # Keep the original targeted warnings separate for assertions.
        page.on(
            "console",
            lambda message: (
                chart_warnings.append(f"{current_surface['name']}: {message.text}")
                if "Scale bindings are currently only supported" in message.text
                or "Infinite extent for field" in message.text
                else None
            ),
        )
        try:
            page.goto(streamlit_server.url, wait_until="domcontentloaded")
            brand_button = page.get_by_role("button", name="Stop-GIS", exact=True)
            brand_button.wait_for(timeout=30_000)
            page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
                timeout=30_000
            )
            confirm_seed_project_open(page)
            page.get_by_role("heading", name="Project Data", exact=True).wait_for(
                timeout=30_000
            )
            playwright_api.expect(
                page.get_by_role("heading", name="Data Quality", exact=True)
            ).to_have_count(0)
            playwright_api.expect(
                page.get_by_role("heading", name="Terminology", exact=True)
            ).to_have_count(0)

            navigate_workspace_page(page, "Data Quality", "Data Quality")
            page.get_by_text("Filter dataset by validation issue", exact=True).wait_for(
                timeout=30_000
            )
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)

            # Continue the broader navigation test in a fresh frontend session.
            # This keeps Streamlit's validation-filter widget initialization from
            # racing the immediately following popover interaction.
            reconnect_streamlit_page(page, streamlit_server)
            page.get_by_role("button", name="Stop-GIS", exact=True).wait_for(
                timeout=30_000
            )
            confirm_seed_project_open(page)
            page.get_by_role("heading", name="Project Data", exact=True).wait_for(
                timeout=30_000
            )
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)

            page.get_by_role("button", name="Taxonomy", exact=True).click(
                timeout=30_000
            )
            page.get_by_role("heading", name="Assessment Design", exact=True).wait_for(
                timeout=30_000
            )
            page.get_by_role("heading", name="Terminology", exact=True).wait_for(
                timeout=30_000
            )
            page.get_by_role(
                "heading", name="Shade source taxonomy", exact=True
            ).wait_for(timeout=30_000)
            page.get_by_role(
                "heading", name="Shade coverage taxonomy", exact=True
            ).wait_for(timeout=30_000)
            taxonomy_editors = page.locator(
                '.st-key-terminology_table [data-testid="stDataFrame"], '
                '.st-key-shade_source_taxonomy_table [data-testid="stDataFrame"], '
                '.st-key-shade_coverage_taxonomy_table [data-testid="stDataFrame"]'
            )
            playwright_api.expect(taxonomy_editors).to_have_count(0, timeout=30_000)
            for table_key in [
                "terminology_table",
                "shade_source_taxonomy_table",
                "shade_coverage_taxonomy_table",
            ]:
                playwright_api.expect(
                    page.locator(f".st-key-{table_key} table.data-page-table")
                ).to_be_visible(timeout=30_000)
            for card_key in [
                "taxonomy_card_terminology",
                "taxonomy_card_source",
                "taxonomy_card_coverage",
            ]:
                playwright_api.expect(
                    page.locator(f".st-key-{card_key}")
                ).to_be_visible(timeout=30_000)

            page.get_by_role("button", name="Edit", exact=True).first.click(
                timeout=30_000
            )
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)
            playwright_api.expect(
                page.locator('.st-key-terminology_table [data-testid="stDataFrame"]')
            ).to_have_count(1, timeout=30_000)
            playwright_api.expect(
                page.get_by_role("button", name="Done", exact=True)
            ).to_be_visible(timeout=30_000)
            page.get_by_role("button", name="Done", exact=True).click(timeout=30_000)
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)
            page.get_by_role("button", name="Edit", exact=True).nth(1).click(
                timeout=30_000
            )
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)
            reset_definitions = page.get_by_role(
                "button", name="Reset definitions", exact=True
            )
            playwright_api.expect(reset_definitions).to_be_visible(timeout=30_000)
            playwright_api.expect(
                page.locator(
                    '.st-key-shade_source_taxonomy_table [data-testid="stDataFrame"]'
                )
            ).to_have_count(1, timeout=30_000)
            playwright_api.expect(
                page.get_by_role("button", name="Dataset", exact=True)
            ).to_be_enabled(timeout=30_000)
            playwright_api.expect(
                page.get_by_role("button", name="Build", exact=True)
            ).to_be_enabled(timeout=30_000)
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)

            for nav_label, heading in expected_pages.items():
                current_surface["name"] = nav_label
                for attempt in range(2):
                    navigation_error: Exception | None = None
                    try:
                        navigate_workspace_page(page, nav_label, heading)
                        break
                    except Exception as error:
                        navigation_error = error

                    if attempt == 1:
                        assert navigation_error is not None
                        raise navigation_error

                    # A healthy server does not guarantee that the browser's current
                    # Streamlit WebSocket session recovered. When that session is
                    # wedged, every button remains disabled indefinitely and retrying
                    # against the same DOM cannot succeed. Reconnect the page before
                    # resolving a fresh locator for the second attempt. The UI fixture
                    # uses a temporary durable project store, so a replacement frontend
                    # session still loads the same test project.
                    reconnect_streamlit_page(page, streamlit_server)
                    page.get_by_role("button", name="Stop-GIS", exact=True).wait_for(
                        timeout=30_000
                    )
                    confirm_seed_project_open(page)
                    page.get_by_role(
                        "heading", name="Project Data", exact=True
                    ).wait_for(timeout=30_000)
                    wait_for_streamlit_idle(playwright_api, page, streamlit_server)
                wait_for_streamlit_idle(playwright_api, page, streamlit_server)
                if nav_label == "Dataset Review":
                    playwright_api.expect(
                        page.get_by_role("button", name="Adjudicate", exact=True)
                    ).to_be_visible(timeout=30_000)
                    page.get_by_role("button", name="Adjudicate", exact=True).click(
                        timeout=30_000
                    )
                    page.get_by_text(
                        "Submit at least one independent assessment before adjudicating.",
                        exact=True,
                    ).wait_for(timeout=30_000)
                    page.get_by_role("button", name="+ Submit Label", exact=True).click(
                        timeout=30_000
                    )
                    wait_for_streamlit_idle(playwright_api, page, streamlit_server)

                    coverage_control = page.get_by_test_id("stSelectbox").filter(
                        has_text="Coverage"
                    )
                    choose_streamlit_selectbox_option(
                        playwright_api,
                        page,
                        coverage_control,
                        "No Shade",
                    )
                    wait_for_streamlit_idle(playwright_api, page, streamlit_server)
                    natural_source = (
                        page.get_by_test_id("stCheckbox")
                        .filter(has_text="Natural")
                        .get_by_role("checkbox")
                    )
                    playwright_api.expect(natural_source).to_be_disabled(timeout=30_000)

                    choose_streamlit_selectbox_option(
                        playwright_api,
                        page,
                        coverage_control,
                        "Limited Shade",
                    )
                    wait_for_streamlit_idle(playwright_api, page, streamlit_server)
                    natural_source = (
                        page.get_by_test_id("stCheckbox")
                        .filter(has_text="Natural")
                        .get_by_role("checkbox")
                    )
                    playwright_api.expect(natural_source).to_be_enabled(timeout=30_000)
                elif nav_label == "Intercoder Review":
                    for section_heading in [
                        "Study Setup",
                        "Review Materials",
                        "Study Progress",
                    ]:
                        playwright_api.expect(
                            page.get_by_role(
                                "heading", name=section_heading, exact=True
                            )
                        ).to_be_visible(timeout=30_000)
                    playwright_api.expect(
                        page.get_by_text(
                            "Reviewers cannot see existing ratings until they submit their own.",
                            exact=True,
                        )
                    ).to_be_visible(timeout=30_000)
                elif nav_label == "Community Voting":
                    playwright_api.expect(
                        page.get_by_role("heading", name="Configuration", exact=True)
                    ).to_be_visible(timeout=30_000)
                    playwright_api.expect(
                        page.get_by_role("heading", name="Live Preview", exact=True)
                    ).to_be_visible(timeout=30_000)
                    voting_toggle_container = page.get_by_test_id("stCheckbox").filter(
                        has_text="Enable visitor voting"
                    )
                    voting_toggle = voting_toggle_container.get_by_role("checkbox")
                    if not voting_toggle.is_checked():
                        playwright_api.expect(
                            page.get_by_text("Visitor Experience", exact=True)
                        ).to_have_count(0, timeout=30_000)
                    if not voting_toggle.is_checked():
                        voting_toggle_container.click()
                    playwright_api.expect(voting_toggle).to_be_checked(timeout=30_000)
                    playwright_api.expect(
                        page.get_by_text("Visitor Experience", exact=True)
                    ).to_be_visible(timeout=30_000)
                    playwright_api.expect(
                        page.get_by_text(
                            "Community voting is currently hidden in the deployed app. Enable it to publish this interface.",
                            exact=True,
                        )
                    ).to_have_count(0, timeout=60_000)
                    wait_for_streamlit_idle(playwright_api, page, streamlit_server)
                elif nav_label == "Visuals":
                    marker_shape_control = page.get_by_test_id("stSelectbox").filter(
                        has_text="Marker shape"
                    )
                    marker_shape = marker_shape_control.get_by_role("combobox")
                    marker_shape.click()
                    page.get_by_role("option", name="Square", exact=True).click()
                    wait_for_streamlit_idle(playwright_api, page, streamlit_server)
                    marker_shape = marker_shape_control.get_by_role("combobox")
                    selected_value = marker_shape.input_value().strip()
                    selected_label = marker_shape.get_attribute("aria-label") or ""
                    # Streamlit's selectbox markup changed within the supported
                    # 1.x range: React Aria exposes the selection as the input
                    # value, while BaseWeb included it in the accessible label.
                    assert (
                        selected_value == "Square"
                        or selected_label == "Selected Square. Marker shape"
                    )

                    marker_size = page.get_by_role("slider", name="Marker size")
                    marker_size.press("ArrowRight")
                    wait_for_streamlit_idle(playwright_api, page, streamlit_server)
                    marker_size = page.get_by_role("slider", name="Marker size")
                    playwright_api.expect(marker_size).to_have_attribute(
                        "aria-valuetext", "8", timeout=30_000
                    )

                    map_chart = page.get_by_test_id("stDeckGlJsonChart").first
                    playwright_api.expect(map_chart).to_be_visible(timeout=30_000)
                    page.wait_for_timeout(1_500)
                    icon_layer_errors = [
                        message
                        for kind, message in chart_events
                        if kind in {"error", "pageerror"} and "IconLayer" in message
                    ]
                    assert icon_layer_errors == []
                elif nav_label == "Preview":
                    map_tab = page.get_by_role("tab", name="Map", exact=True)
                    playwright_api.expect(map_tab).to_have_attribute(
                        "aria-selected", "true", timeout=60_000
                    )
                    stop_details_tab = page.get_by_role(
                        "tab", name="Stop details", exact=True
                    )
                    if stop_details_tab.count() > 0:
                        stop_details_tab.click()
                    playwright_api.expect(
                        page.get_by_role("heading", name="Stop Details", exact=True)
                    ).to_be_visible(timeout=60_000)
                    analytics_tab = page.get_by_role(
                        "tab", name="Analytics", exact=True
                    )
                    analytics_tab.click()
                    playwright_api.expect(analytics_tab).to_have_attribute(
                        "aria-selected", "true", timeout=60_000
                    )
                    playwright_api.expect(
                        page.get_by_role(
                            "heading", name="Summary Statistics", exact=True
                        )
                    ).to_be_visible(timeout=60_000)
                    wait_for_streamlit_idle(playwright_api, page, streamlit_server)
                elif nav_label == "Deploy":
                    playwright_api.expect(
                        page.get_by_role("heading", name="Publish website", exact=True)
                    ).to_be_visible(timeout=30_000)
                    playwright_api.expect(
                        page.get_by_text(
                            "Complete settings before publishing", exact=True
                        )
                    ).to_be_visible(timeout=30_000)
                    playwright_api.expect(
                        page.get_by_role("button", name="Publish website", exact=True)
                    ).to_have_count(0)
                    playwright_api.expect(
                        page.get_by_text(
                            "Enter your GitHub username and destination repository before publishing.",
                            exact=True,
                        )
                    ).to_be_visible(timeout=30_000)

                    settings = page.get_by_text("Settings", exact=True)
                    settings.click()
                    username_input = page.get_by_label("GitHub username", exact=True)
                    repository_input = page.get_by_label(
                        "Destination repository", exact=True
                    )
                    playwright_api.expect(username_input).to_have_value("")
                    playwright_api.expect(repository_input).to_have_value("")
                    playwright_api.expect(
                        page.get_by_label("Default branch", exact=True)
                    ).to_have_value("main")
                    playwright_api.expect(
                        page.get_by_label("Commit message", exact=True)
                    ).to_have_value("Publish website update")
                    username_input.fill("example-owner")
                    username_input.press("Tab")
                    wait_for_streamlit_idle(playwright_api, page, streamlit_server)
                    repository_input = page.get_by_label(
                        "Destination repository", exact=True
                    )
                    repository_input.fill("shade-study-site")
                    repository_input.press("Tab")
                    wait_for_streamlit_idle(playwright_api, page, streamlit_server)
                    reload_button = page.get_by_role(
                        "button", name="Reload saved project", exact=True
                    )
                    if reload_button.count() > 0:
                        playwright_api.expect(reload_button).to_be_visible(
                            timeout=30_000
                        )
                        # The UI fixture intentionally disables persistence. Editing
                        # project settings can therefore trigger the production stale-
                        # session guard; that guard is the correct deploy outcome here.
                        continue
                    playwright_api.expect(
                        page.get_by_text("Ready to publish", exact=True)
                    ).to_be_visible(timeout=30_000)
                    publish_button = page.get_by_role(
                        "button", name="Publish website", exact=True
                    )
                    playwright_api.expect(publish_button).to_be_enabled(timeout=30_000)
                    for stage in [
                        "Check project",
                        "Prepare website",
                        "Publish",
                        "Verify website",
                    ]:
                        playwright_api.expect(
                            page.get_by_text(stage, exact=True)
                        ).to_have_count(0)
                    playwright_api.expect(
                        page.get_by_text("Waiting", exact=True)
                    ).to_have_count(0)
                    playwright_api.expect(
                        page.get_by_text("This usually takes 1–3 minutes.", exact=True)
                    ).to_be_visible(timeout=30_000)
                    page.get_by_text("Settings", exact=True).click()
                    playwright_api.expect(
                        page.get_by_role(
                            "button", name="Download website package", exact=True
                        )
                    ).to_be_visible(timeout=30_000)
                    playwright_api.expect(
                        page.get_by_text("Set-StrictMode -Version Latest", exact=False)
                    ).to_be_visible(timeout=30_000)

            confirm_main_menu_return(page)
            playwright_api.expect(
                page.get_by_role("heading", name="Your Projects", exact=True)
            ).to_be_visible(timeout=30_000)
            assert chart_warnings == []
            assert streamlit_server.process.poll() is None
        except Exception as error:
            # Save debugging artifacts: screenshot, full HTML, body text, and console log.
            try:
                dump_dir = Path(tempfile.gettempdir()) / "shade_gis_ui_failure_logs"
                dump_dir.mkdir(parents=True, exist_ok=True)
                basename = f"ui_failure_{int(time.time())}_{uuid.uuid4().hex}"
                screenshot_path = dump_dir / f"{basename}.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                html_path = dump_dir / f"{basename}.html"
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(page.content())
                text_path = dump_dir / f"{basename}.txt"
                with open(text_path, "w", encoding="utf-8") as f:
                    f.write(page.locator("body").inner_text())
                console_path = dump_dir / f"{basename}_console.txt"
                with open(console_path, "w", encoding="utf-8") as f:
                    for kind, msg in chart_events:
                        f.write(f"{kind}: {msg}\n")
            except Exception:
                # Best-effort debug dump; ignore failures here to not hide original error.
                pass
            raise AssertionError(
                f"UI failure while testing {current_surface['name']}: {error}"
                f"\n\nStreamlit server log tail:\n{streamlit_server.log_tail()}"
            ) from error
        finally:
            browser.close()


def test_mobile_workspace_navigation_wraps_without_page_overflow(
    playwright_api, streamlit_server: StreamlitServer
):
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            page.goto(streamlit_server.url, wait_until="domcontentloaded")
            page.get_by_role("heading", name="Your Projects", exact=True).wait_for(
                timeout=30_000
            )
            page.locator('div[class*="st-key-home_open_"] button').first.click(
                timeout=30_000
            )
            page.get_by_role("heading", name="Project Data", exact=True).wait_for(
                timeout=30_000
            )
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)

            page.get_by_role("button", name="Labeling", exact=True).click(
                timeout=30_000
            )
            page.get_by_role("heading", name="Dataset Review", exact=True).wait_for(
                timeout=30_000
            )
            wait_for_streamlit_idle(playwright_api, page, streamlit_server)

            playwright_api.expect(
                page.locator(".st-key-label_action_queue button")
            ).to_have_attribute("kind", "primary", timeout=30_000)
            has_page_overflow = page.evaluate(
                "document.documentElement.scrollWidth > window.innerWidth + 2"
            )
            assert has_page_overflow is False
        finally:
            browser.close()
