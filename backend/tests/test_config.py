"""Focused tests for typed local configuration and path safety."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import (
    REPOSITORY_ROOT,
    AppSettings,
    ConfigurationError,
    initialize_directories,
    load_settings,
)
from app.main import create_app


@pytest.fixture
def local_test_data_root(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Provide an ignored test directory that remains inside the repository."""
    relative_root = Path("data/.test-settings")
    absolute_root = REPOSITORY_ROOT / relative_root
    shutil.rmtree(absolute_root, ignore_errors=True)
    monkeypatch.setenv("AIOS_PATHS__DATA_ROOT", relative_root.as_posix())
    try:
        yield absolute_root
    finally:
        shutil.rmtree(absolute_root, ignore_errors=True)


def test_default_settings_encode_hard_resource_policy() -> None:
    """Defaults leave Windows capacity and never claim the whole laptop."""
    settings = load_settings()

    assert settings.resources.non_llm_application_ram_mb == 2048
    assert settings.resources.normal_project_ram_mb == 6144
    assert settings.resources.absolute_project_peak_ram_mb == 8192
    assert settings.resources.windows_reserved_ram_mb == 8192
    assert settings.resources.vram_target_mb == 6656
    assert settings.resources.source_download_concurrency == 3
    assert settings.resources.llm_generation_concurrency == 1
    assert settings.ollama.on_demand_only is True
    assert settings.ollama.unload_after_generation is True
    assert settings.models.scout.keep_alive_seconds == 0
    assert settings.models.analyst.keep_alive_seconds == 0


@pytest.mark.parametrize(
    ("environment_name", "invalid_value", "expected_field"),
    [
        ("AIOS_RESOURCES__NON_LLM_APPLICATION_RAM_MB", "2049", "non_llm_application_ram_mb"),
        ("AIOS_RESOURCES__NORMAL_PROJECT_RAM_MB", "6145", "normal_project_ram_mb"),
        ("AIOS_RESOURCES__ABSOLUTE_PROJECT_PEAK_RAM_MB", "8193", "absolute_project_peak_ram_mb"),
        ("AIOS_RESOURCES__WINDOWS_RESERVED_RAM_MB", "8191", "windows_reserved_ram_mb"),
        ("AIOS_RESOURCES__VRAM_TARGET_MB", "6657", "vram_target_mb"),
        ("AIOS_RESOURCES__SOURCE_DOWNLOAD_CONCURRENCY", "4", "source_download_concurrency"),
        ("AIOS_RESOURCES__LLM_GENERATION_CONCURRENCY", "2", "llm_generation_concurrency"),
    ],
)
def test_resource_policy_rejects_values_above_hard_limits(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    invalid_value: str,
    expected_field: str,
) -> None:
    """Environment overrides cannot weaken the laptop safety policy."""
    monkeypatch.setenv(environment_name, invalid_value)

    with pytest.raises(ConfigurationError, match=expected_field):
        load_settings()


@pytest.mark.parametrize(
    ("environment_name", "invalid_value", "expected_text"),
    [
        ("AIOS_OLLAMA__BASE_URL", "https://example.com", "base_url"),
        ("AIOS_OLLAMA__ON_DEMAND_ONLY", "false", "on_demand_only"),
        ("AIOS_OLLAMA__UNLOAD_AFTER_GENERATION", "false", "unload_after_generation"),
        ("AIOS_MODELS__SCOUT__KEEP_ALIVE_SECONDS", "1", "keep_alive_seconds"),
        ("AIOS_MODELS__EMBED__BATCH_SIZE", "9", "batch_size"),
    ],
)
def test_model_policy_cannot_enable_persistent_or_unbounded_work(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    invalid_value: str,
    expected_text: str,
) -> None:
    """Model configuration remains on-demand, unloadable, and bounded."""
    monkeypatch.setenv(environment_name, invalid_value)

    with pytest.raises(ConfigurationError, match=expected_text):
        load_settings()


def test_profile_cannot_exceed_global_token_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-model token limits cannot bypass the global context ceiling."""
    monkeypatch.setenv("AIOS_MODELS__SCOUT__MAXIMUM_CONTEXT_TOKENS", "13000")

    with pytest.raises(ConfigurationError, match="scout context exceeds"):
        load_settings()


@pytest.mark.parametrize(
    ("environment_name", "invalid_path", "expected_field"),
    [
        ("AIOS_PATHS__PROJECT_ROOT", "../..", "project_root"),
        ("AIOS_PATHS__DATA_ROOT", "../outside-data", "data_root"),
        ("AIOS_PATHS__DATABASE_PATH", "../outside.db", "database_path"),
        ("AIOS_PATHS__RAW_DOCUMENTS_ROOT", "../outside-raw", "raw_documents_root"),
    ],
)
def test_paths_cannot_escape_configured_roots(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    invalid_path: str,
    expected_field: str,
) -> None:
    """Traversal-like configuration fails before filesystem initialization."""
    monkeypatch.setenv(environment_name, invalid_path)

    with pytest.raises(ConfigurationError, match=expected_field):
        load_settings()


def test_directory_initialization_is_explicit_and_idempotent(local_test_data_root: Path) -> None:
    """All configured directories are created inside data_root on repeated calls."""
    settings = load_settings()

    assert not local_test_data_root.exists()
    initialize_directories(settings.paths)
    initialize_directories(settings.paths)

    assert settings.paths.data_root == local_test_data_root
    assert all(path.is_dir() for path in settings.paths.required_directories)
    assert settings.paths.database_path.parent.is_dir()
    assert all(
        path.is_relative_to(local_test_data_root) for path in settings.paths.required_directories
    )


def test_secret_is_loaded_but_redacted_from_settings_representation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environment-only source credentials never appear in settings repr."""
    secret = "github-test-token-do-not-log"
    monkeypatch.setenv("AIOS_SOURCES__GITHUB_TOKEN", secret)

    settings = load_settings()
    representation = repr(settings)

    assert settings.sources.github_token is not None
    assert settings.sources.github_token.get_secret_value() == secret
    assert secret not in representation
    assert "**********" in representation


def test_invalid_settings_fail_during_application_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed startup configuration raises an actionable error immediately."""
    monkeypatch.setenv("AIOS_HTTP__MAXIMUM_RETRIES", "not-an-integer")

    with pytest.raises(ConfigurationError, match="maximum_retries"):
        create_app()


def test_settings_are_immutable() -> None:
    """Validated configuration cannot drift during a process lifetime."""
    settings = load_settings()

    with pytest.raises(ValidationError, match="frozen"):
        settings.resources.source_download_concurrency = 2  # type: ignore[misc]


def test_app_settings_type_is_stable() -> None:
    """The loader exposes the declared settings boundary."""
    assert isinstance(load_settings(), AppSettings)
