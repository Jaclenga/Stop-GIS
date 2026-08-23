from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANDROID_NAMESPACE = "{http://schemas.android.com/apk/res/android}"


def test_android_manifest_exposes_camera_capture_without_requiring_hardware():
    manifest = ElementTree.parse(
        PROJECT_ROOT / "android_app" / "app" / "src" / "main" / "AndroidManifest.xml"
    ).getroot()

    features = manifest.findall("uses-feature")
    assert any(
        feature.get(f"{ANDROID_NAMESPACE}name") == "android.hardware.camera"
        and feature.get(f"{ANDROID_NAMESPACE}required") == "false"
        for feature in features
    )
    query_actions = manifest.findall("./queries/intent/action")
    assert any(
        action.get(f"{ANDROID_NAMESPACE}name") == "android.media.action.IMAGE_CAPTURE"
        for action in query_actions
    )


def test_android_base_themes_do_not_use_api_27_only_attributes():
    theme_paths = [
        PROJECT_ROOT / "android_app" / "app" / "src" / "main" / "res" / directory / "themes.xml"
        for directory in ("values", "values-night")
    ]

    for theme_path in theme_paths:
        source = theme_path.read_text(encoding="utf-8")
        assert "android:navigationBarDividerColor" not in source


def test_observation_repository_serializes_updates_and_cleans_prior_process_photos():
    source = (
        PROJECT_ROOT
        / "android_app"
        / "app"
        / "src"
        / "main"
        / "kotlin"
        / "org"
        / "shadegis"
        / "mobile"
        / "ObservationRepository.kt"
    ).read_text(encoding="utf-8")

    assert "private val mutex = Mutex()" in source
    assert source.count("mutex.withLock") == 2
    assert "cleanupOrphanedPhotos(loaded.observations)" in source
    assert "photo.lastModified() < repositoryStartedAt" in source
