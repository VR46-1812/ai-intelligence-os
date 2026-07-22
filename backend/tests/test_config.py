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
    SourceSettings,
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
    assert settings.ocr.enabled is False
    assert settings.ocr.suspicious_native_characters == 40
    assert settings.sources.openreview_enabled is True
    assert settings.sources.openreview_venues == ("ICLR.cc/2026/Conference",)
    assert settings.sources.huggingface_enabled is True
    assert settings.sources.rss_enabled is True
    assert all(feed.startswith("https://") for feed in settings.sources.rss_feeds)
    assert settings.sources.reddit_feeds == ("https://www.reddit.com/r/LocalLLaMA/.rss",)
    assert settings.sources.github_search_queries == ("topic:llm stars:>100",)
    assert settings.sources.youtube_feeds == ()


def test_multisource_allowlists_reject_empty_or_insecure_configuration() -> None:
    with pytest.raises(ValidationError, match="openreview_venues"):
        SourceSettings(openreview_venues=())
    with pytest.raises(ValidationError, match="HTTPS"):
        SourceSettings(rss_feeds=("http://example.test/feed.xml",))
    with pytest.raises(ValidationError, match="limited to 20"):
        SourceSettings(
            watchlist_feeds=tuple(f"https://example.test/{index}" for index in range(21))
        )
    with pytest.raises(ValidationError, match="bounded limits"):
        SourceSettings(github_search_queries=tuple(f"topic:ai-{index}" for index in range(11)))


def test_optional_ocr_configuration_is_typed_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIOS_OCR__ENABLED", "true")
    monkeypatch.setenv("AIOS_OCR__LANGUAGE", "eng+hin")
    monkeypatch.setenv("AIOS_OCR__PAGE_TIMEOUT_SECONDS", "45")

    settings = load_settings()

    assert settings.ocr.enabled is True
    assert settings.ocr.language == "eng+hin"
    assert settings.ocr.page_timeout_seconds == 45


def test_invalid_ocr_configuration_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIOS_OCR__PAGE_TIMEOUT_SECONDS", "500")

    with pytest.raises(ConfigurationError, match="page_timeout_seconds"):
        load_settings()


def test_arxiv_categories_default_to_the_phase_one_allowlist() -> None:
    settings = load_settings()

    assert settings.sources.arxiv_categories == (
        "cs.AI",
        "cs.LG",
        "cs.CL",
        "cs.CV",
        "cs.RO",
        "stat.ML",
    )


def test_arxiv_category_allowlist_can_be_narrowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIOS_SOURCES__ARXIV_CATEGORIES", '["cs.AI", "cs.CL"]')

    assert load_settings().sources.arxiv_categories == ("cs.AI", "cs.CL")


@pytest.mark.parametrize(
    "configured",
    ("[]", '["cs.AI", "cs.AI"]', '["cs.AI", "econ.EM"]'),
)
def test_arxiv_categories_reject_empty_duplicate_or_out_of_scope_values(
    monkeypatch: pytest.MonkeyPatch, configured: str
) -> None:
    monkeypatch.setenv("AIOS_SOURCES__ARXIV_CATEGORIES", configured)

    with pytest.raises(ConfigurationError, match="arxiv_categories"):
        load_settings()


def test_daily_work_limits_have_bounded_defaults() -> None:
    """Daily defaults limit broad briefs and expensive automatic analysis."""
    settings = load_settings()

    assert settings.daily_work.maximum_fast_briefs == 10
    assert settings.daily_work.maximum_automatic_deep_dives == 2
    assert settings.scheduler.enabled is True
    assert settings.scheduler.timezone == "Asia/Kolkata"
    assert settings.scheduler.maximum_records == 5
    assert settings.scheduler.document_limit == 5
    assert settings.scheduler.top_briefs == 1


def test_daily_work_limits_accept_valid_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators can lower or adjust daily limits within the approved bounds."""
    monkeypatch.setenv("AIOS_DAILY_WORK__MAXIMUM_FAST_BRIEFS", "12")
    monkeypatch.setenv("AIOS_DAILY_WORK__MAXIMUM_AUTOMATIC_DEEP_DIVES", "3")

    settings = load_settings()

    assert settings.daily_work.maximum_fast_briefs == 12
    assert settings.daily_work.maximum_automatic_deep_dives == 3


@pytest.mark.parametrize(
    "environment_name",
    ["AIOS_SCHEDULER__MAXIMUM_RECORDS", "AIOS_SCHEDULER__DOCUMENT_LIMIT"],
)
def test_v1_daily_source_and_document_bounds_cannot_exceed_five(
    monkeypatch: pytest.MonkeyPatch, environment_name: str
) -> None:
    monkeypatch.setenv(environment_name, "6")

    with pytest.raises(ConfigurationError, match=environment_name.split("__")[-1].casefold()):
        load_settings()


@pytest.mark.parametrize(
    ("environment_name", "invalid_value", "expected_field"),
    [
        ("AIOS_DAILY_WORK__MAXIMUM_FAST_BRIEFS", "0", "maximum_fast_briefs"),
        ("AIOS_DAILY_WORK__MAXIMUM_FAST_BRIEFS", "26", "maximum_fast_briefs"),
        (
            "AIOS_DAILY_WORK__MAXIMUM_AUTOMATIC_DEEP_DIVES",
            "0",
            "maximum_automatic_deep_dives",
        ),
        (
            "AIOS_DAILY_WORK__MAXIMUM_AUTOMATIC_DEEP_DIVES",
            "4",
            "maximum_automatic_deep_dives",
        ),
    ],
)
def test_daily_work_limits_reject_values_outside_bounds(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    invalid_value: str,
    expected_field: str,
) -> None:
    """Daily generation counts cannot exceed or fall below approved bounds."""
    monkeypatch.setenv(environment_name, invalid_value)

    with pytest.raises(ConfigurationError, match=expected_field):
        load_settings()


def test_daily_deep_dives_cannot_exceed_fast_briefs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The expensive stage cannot contain more items than its shortlist."""
    monkeypatch.setenv("AIOS_DAILY_WORK__MAXIMUM_FAST_BRIEFS", "1")
    monkeypatch.setenv("AIOS_DAILY_WORK__MAXIMUM_AUTOMATIC_DEEP_DIVES", "2")

    with pytest.raises(
        ConfigurationError,
        match="maximum_automatic_deep_dives cannot exceed maximum_fast_briefs",
    ):
        load_settings()


def test_database_settings_enforce_sqlite_defaults() -> None:
    """SQLite defaults require WAL, foreign keys, and bounded lock waiting."""
    settings = load_settings()

    assert settings.database.journal_mode == "WAL"
    assert settings.database.foreign_keys is True
    assert settings.database.busy_timeout_ms == 5000


@pytest.mark.parametrize(
    ("environment_name", "invalid_value", "expected_field"),
    [
        ("AIOS_DATABASE__JOURNAL_MODE", "DELETE", "journal_mode"),
        ("AIOS_DATABASE__FOREIGN_KEYS", "false", "foreign_keys"),
        ("AIOS_DATABASE__BUSY_TIMEOUT_MS", "999", "busy_timeout_ms"),
        ("AIOS_DATABASE__BUSY_TIMEOUT_MS", "30001", "busy_timeout_ms"),
    ],
)
def test_database_settings_cannot_weaken_sqlite_policy(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
    invalid_value: str,
    expected_field: str,
) -> None:
    """Environment overrides cannot disable mandatory SQLite safeguards."""
    monkeypatch.setenv(environment_name, invalid_value)

    with pytest.raises(ConfigurationError, match=expected_field):
        load_settings()


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
