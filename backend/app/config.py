"""Typed, local-only application configuration for phase one."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MEBIBYTE = 1024 * 1024
GIBIBYTE_IN_MEBIBYTES = 1024
ArxivCategory = Literal["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.RO", "stat.ML"]


class ConfigurationError(RuntimeError):
    """Raised when environment-backed application settings are invalid."""


class DirectoryInitializationError(RuntimeError):
    """Raised when a configured local data directory cannot be initialized."""


def _resolve_within(path: Path, root: Path, field_name: str) -> Path:
    """Resolve a path and reject values outside the required root."""
    resolved_root = root.resolve(strict=False)
    candidate = path if path.is_absolute() else resolved_root / path
    resolved_candidate = candidate.resolve(strict=False)
    if not resolved_candidate.is_relative_to(resolved_root):
        msg = f"{field_name} must remain inside {resolved_root}"
        raise ValueError(msg)
    return resolved_candidate


class PathSettings(BaseModel):
    """Repository-confined filesystem locations used by the application."""

    model_config = ConfigDict(frozen=True)

    project_root: Path = REPOSITORY_ROOT
    data_root: Path = Path("data")
    database_path: Path = Path("state/ai-intelligence-os.db")
    raw_documents_root: Path = Path("raw")
    processed_documents_root: Path = Path("processed")
    quarantine_root: Path = Path("quarantine")
    temporary_root: Path = Path("temporary")

    @model_validator(mode="after")
    def resolve_and_confine_paths(self) -> Self:
        """Normalize all writable paths and enforce the local data boundary."""
        project_root = self.project_root.resolve(strict=False)
        if project_root != REPOSITORY_ROOT:
            msg = f"project_root is fixed to {REPOSITORY_ROOT}"
            raise ValueError(msg)

        data_root = _resolve_within(self.data_root, project_root, "data_root")
        database_path = _resolve_within(self.database_path, data_root, "database_path")
        if database_path == data_root or database_path.suffix.lower() not in {".db", ".sqlite3"}:
            msg = "database_path must name a .db or .sqlite3 file inside data_root"
            raise ValueError(msg)

        object.__setattr__(self, "project_root", project_root)
        object.__setattr__(self, "data_root", data_root)
        object.__setattr__(self, "database_path", database_path)
        for field_name in (
            "raw_documents_root",
            "processed_documents_root",
            "quarantine_root",
            "temporary_root",
        ):
            value = getattr(self, field_name)
            object.__setattr__(self, field_name, _resolve_within(value, data_root, field_name))
        return self

    @property
    def required_directories(self) -> tuple[Path, ...]:
        """Return all directories that must exist before application work begins."""
        return (
            self.data_root,
            self.database_path.parent,
            self.raw_documents_root,
            self.processed_documents_root,
            self.quarantine_root,
            self.temporary_root,
        )


class DownloadSettings(BaseModel):
    """Bounded document download settings shared by safe acquisition."""

    model_config = ConfigDict(frozen=True)

    maximum_document_bytes: int = Field(default=50 * MEBIBYTE, ge=MEBIBYTE, le=200 * MEBIBYTE)
    chunk_bytes: int = Field(default=64 * 1024, ge=4096, le=MEBIBYTE)
    checksum_algorithm: Literal["sha256"] = "sha256"


class OcrSettings(BaseModel):
    """Optional page-level OCR policy; native text always remains primary."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    tesseract_executable: str = Field(default="tesseract", min_length=1, max_length=260)
    language: str = Field(default="eng", pattern=r"^[A-Za-z0-9_+-]{2,40}$")
    page_timeout_seconds: int = Field(default=30, ge=5, le=120)
    suspicious_native_characters: int = Field(default=40, ge=1, le=500)


class DatabaseSettings(BaseModel):
    """Hard SQLite connection policy for the local persistence layer."""

    model_config = ConfigDict(frozen=True)

    journal_mode: Literal["WAL"] = "WAL"
    foreign_keys: Literal[True] = True
    busy_timeout_ms: int = Field(default=5000, ge=1000, le=30_000)


class HttpSettings(BaseModel):
    """Shared bounded HTTP policy for later source adapters."""

    model_config = ConfigDict(frozen=True)

    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    maximum_retries: int = Field(default=3, ge=0, le=5)
    initial_backoff_seconds: float = Field(default=1.0, gt=0, le=30)
    maximum_backoff_seconds: float = Field(default=30.0, gt=0, le=120)
    user_agent: str = Field(default="ai-intelligence-os/0.1", min_length=5, max_length=128)

    @model_validator(mode="after")
    def validate_backoff_window(self) -> Self:
        """Ensure retry backoff settings form a usable bounded window."""
        if self.initial_backoff_seconds > self.maximum_backoff_seconds:
            msg = "initial_backoff_seconds cannot exceed maximum_backoff_seconds"
            raise ValueError(msg)
        return self


class SourceSettings(BaseModel):
    """Phase-one source enablement and environment-only credentials."""

    model_config = ConfigDict(frozen=True)

    arxiv_enabled: bool = True
    arxiv_categories: tuple[ArxivCategory, ...] = (
        "cs.AI",
        "cs.LG",
        "cs.CL",
        "cs.CV",
        "cs.RO",
        "stat.ML",
    )
    openreview_enabled: bool = True
    github_enrichment_enabled: bool = True
    metadata_overlap_hours: int = Field(default=24, ge=1, le=168)
    github_token: SecretStr | None = None

    @model_validator(mode="after")
    def validate_arxiv_categories(self) -> Self:
        if not self.arxiv_categories:
            raise ValueError("arxiv_categories must include at least one phase-one category")
        if len(self.arxiv_categories) != len(set(self.arxiv_categories)):
            raise ValueError("arxiv_categories must not contain duplicates")
        return self


class OllamaSettings(BaseModel):
    """Local Ollama endpoint policy without performing runtime calls."""

    model_config = ConfigDict(frozen=True)

    base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434")
    request_timeout_seconds: float = Field(default=300.0, gt=0, le=900)
    on_demand_only: Literal[True] = True
    unload_after_generation: Literal[True] = True

    @model_validator(mode="after")
    def require_local_endpoint(self) -> Self:
        """Prevent configuration from redirecting inference to a hosted service."""
        if self.base_url.scheme != "http" or self.base_url.host not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            msg = "base_url must use HTTP on a local loopback host"
            raise ValueError(msg)
        return self


class TokenLimitSettings(BaseModel):
    """Absolute prompt and response ceilings shared by generation profiles."""

    model_config = ConfigDict(frozen=True)

    maximum_context_tokens: int = Field(default=12_288, ge=1024, le=16_384)
    maximum_output_tokens: int = Field(default=3000, ge=128, le=4096)


class GenerationModelProfile(BaseModel):
    """Configuration for a sequential, unloadable local generation model."""

    model_config = ConfigDict(frozen=True)

    runtime: Literal["ollama"] = "ollama"
    model: str = Field(min_length=1, max_length=128)
    maximum_context_tokens: int = Field(ge=1024, le=16_384)
    maximum_output_tokens: int = Field(ge=128, le=4096)
    temperature: float = Field(default=0.1, ge=0, le=1)
    keep_alive_seconds: Literal[0] = 0


class ScoutModelProfile(GenerationModelProfile):
    """Default lightweight classification and routing profile."""

    model: str = Field(default="qwen3:4b", min_length=1, max_length=128)
    maximum_context_tokens: int = Field(default=8192, ge=1024, le=16_384)
    maximum_output_tokens: int = Field(default=1200, ge=128, le=4096)


class AnalystModelProfile(GenerationModelProfile):
    """Default evidence analysis and review profile."""

    model: str = Field(default="qwen3:8b", min_length=1, max_length=128)
    maximum_context_tokens: int = Field(default=12_288, ge=1024, le=16_384)
    maximum_output_tokens: int = Field(default=3000, ge=128, le=4096)


class EmbeddingModelProfile(BaseModel):
    """Configuration for bounded, on-demand local embeddings."""

    model_config = ConfigDict(frozen=True)

    runtime: Literal["ollama"] = "ollama"
    model: str = Field(default="embeddinggemma", min_length=1, max_length=128)
    batch_size: int = Field(default=8, ge=1, le=8)
    keep_alive_seconds: Literal[0] = 0


class ModelProfileSettings(BaseModel):
    """Named local model profiles; definitions do not load or download models."""

    model_config = ConfigDict(frozen=True)

    scout: ScoutModelProfile = Field(default_factory=ScoutModelProfile)
    analyst: AnalystModelProfile = Field(default_factory=AnalystModelProfile)
    embed: EmbeddingModelProfile = Field(default_factory=EmbeddingModelProfile)


class RetentionSettings(BaseModel):
    """Local retention ceilings for later cleanup workflows."""

    model_config = ConfigDict(frozen=True)

    raw_payload_days: int = Field(default=30, ge=1, le=365)
    temporary_file_hours: int = Field(default=24, ge=1, le=168)
    failed_artifact_days: int = Field(default=30, ge=1, le=365)
    maximum_storage_gib: int = Field(default=100, ge=10, le=300)


class DailyWorkLimitSettings(BaseModel):
    """Daily generation-count ceilings for later orchestration work."""

    model_config = ConfigDict(frozen=True)

    maximum_fast_briefs: int = Field(default=10, ge=1, le=25)
    maximum_automatic_deep_dives: int = Field(default=2, ge=1, le=3)

    @model_validator(mode="after")
    def validate_daily_funnel(self) -> Self:
        """Keep expensive deep dives bounded by the fast-brief shortlist."""
        if self.maximum_automatic_deep_dives > self.maximum_fast_briefs:
            msg = "maximum_automatic_deep_dives cannot exceed maximum_fast_briefs"
            raise ValueError(msg)
        return self


class ResourceBudgetSettings(BaseModel):
    """Hard laptop resource ceilings for all later runtime work."""

    model_config = ConfigDict(frozen=True)

    assumed_system_ram_mb: Literal[16_384] = 16_384
    non_llm_application_ram_mb: int = Field(default=2048, ge=256, le=2048)
    normal_project_ram_mb: int = Field(default=6144, ge=1024, le=6144)
    absolute_project_peak_ram_mb: int = Field(default=8192, ge=2048, le=8192)
    windows_reserved_ram_mb: int = Field(default=8192, ge=8192, le=12_288)
    vram_target_mb: int = Field(default=int(6.5 * GIBIBYTE_IN_MEBIBYTES), ge=1024, le=6656)
    source_download_concurrency: int = Field(default=3, ge=1, le=3)
    llm_generation_concurrency: Literal[1] = 1

    @model_validator(mode="after")
    def validate_combined_budgets(self) -> Self:
        """Reserve Windows capacity and preserve the ordered project ceilings."""
        if self.non_llm_application_ram_mb > self.normal_project_ram_mb:
            msg = "non_llm_application_ram_mb cannot exceed normal_project_ram_mb"
            raise ValueError(msg)
        if self.normal_project_ram_mb > self.absolute_project_peak_ram_mb:
            msg = "normal_project_ram_mb cannot exceed absolute_project_peak_ram_mb"
            raise ValueError(msg)
        available_project_ram = self.assumed_system_ram_mb - self.windows_reserved_ram_mb
        if self.absolute_project_peak_ram_mb > available_project_ram:
            msg = "absolute_project_peak_ram_mb must leave windows_reserved_ram_mb available"
            raise ValueError(msg)
        return self


class AppSettings(BaseSettings):
    """Single typed configuration boundary for the modular monolith."""

    model_config = SettingsConfigDict(
        env_prefix="AIOS_",
        env_nested_delimiter="__",
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        nested_model_default_partial_update=True,
    )

    paths: PathSettings = Field(default_factory=PathSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    downloads: DownloadSettings = Field(default_factory=DownloadSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    sources: SourceSettings = Field(default_factory=SourceSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    token_limits: TokenLimitSettings = Field(default_factory=TokenLimitSettings)
    models: ModelProfileSettings = Field(default_factory=ModelProfileSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    daily_work: DailyWorkLimitSettings = Field(default_factory=DailyWorkLimitSettings)
    resources: ResourceBudgetSettings = Field(default_factory=ResourceBudgetSettings)

    @model_validator(mode="after")
    def validate_model_token_limits(self) -> Self:
        """Ensure every generation profile stays inside global token ceilings."""
        for profile_name, profile in (
            ("scout", self.models.scout),
            ("analyst", self.models.analyst),
        ):
            if profile.maximum_context_tokens > self.token_limits.maximum_context_tokens:
                msg = f"{profile_name} context exceeds token_limits.maximum_context_tokens"
                raise ValueError(msg)
            if profile.maximum_output_tokens > self.token_limits.maximum_output_tokens:
                msg = f"{profile_name} output exceeds token_limits.maximum_output_tokens"
                raise ValueError(msg)
        return self


def load_settings() -> AppSettings:
    """Load environment settings and expose failures as an actionable startup error."""
    try:
        return AppSettings()
    except ValidationError as error:
        raise ConfigurationError(f"Invalid AI Intelligence OS configuration:\n{error}") from error


def initialize_directories(paths: PathSettings) -> None:
    """Idempotently create only the validated local data directories."""
    for directory in paths.required_directories:
        resolved_directory = directory.resolve(strict=False)
        if not resolved_directory.is_relative_to(paths.data_root):
            raise DirectoryInitializationError(
                f"Refusing to initialize directory outside data_root: {resolved_directory}"
            )
        try:
            resolved_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise DirectoryInitializationError(
                f"Could not initialize data directory {resolved_directory}: {error}"
            ) from error
