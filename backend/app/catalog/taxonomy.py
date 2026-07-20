"""Versioned controlled topic taxonomy and deterministic source mappings."""

from __future__ import annotations

from importlib.resources import files
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain.models import Topic
from app.domain.repositories import TopicRepository


class TaxonomyError(ValueError):
    """Raised when a controlled taxonomy definition is inconsistent."""


class TaxonomyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TopicDefinition(TaxonomyModel):
    topic_key: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parent_topic_key: str | None = None
    active: bool = True


class SourceCategoryMapping(TaxonomyModel):
    source_key: str = Field(min_length=1)
    source_category: str = Field(min_length=1)
    topic_keys: tuple[str, ...] = Field(min_length=1)


class TopicTaxonomy(TaxonomyModel):
    schema_version: int = Field(ge=1)
    taxonomy_version: str = Field(pattern=r"^\d{4}\.\d+$")
    topics: tuple[TopicDefinition, ...] = Field(min_length=1)
    source_category_mappings: tuple[SourceCategoryMapping, ...]
    user_weights: dict[str, float]

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        topic_keys = [topic.topic_key for topic in self.topics]
        if len(topic_keys) != len(set(topic_keys)):
            raise ValueError("topic keys must be unique")
        available = set(topic_keys)
        if "unknown" not in available:
            raise ValueError("taxonomy must define the unknown topic")
        for topic in self.topics:
            if topic.parent_topic_key is not None and topic.parent_topic_key not in available:
                raise ValueError(f"unknown parent topic: {topic.parent_topic_key}")
        self._validate_acyclic_parents()

        mapping_keys: set[tuple[str, str]] = set()
        for mapping in self.source_category_mappings:
            mapping_key = (
                mapping.source_key.strip().casefold(),
                mapping.source_category.strip().casefold(),
            )
            if mapping_key in mapping_keys:
                raise ValueError(f"duplicate source-category mapping: {mapping_key}")
            mapping_keys.add(mapping_key)
            missing = set(mapping.topic_keys) - available
            if missing:
                raise ValueError(f"mapping references unknown topics: {sorted(missing)}")

        if set(self.user_weights) != available:
            raise ValueError("user_weights must contain exactly every controlled topic")
        invalid_weights = {
            key: value for key, value in self.user_weights.items() if not 0 <= value <= 1
        }
        if invalid_weights:
            raise ValueError(f"user weights must be between 0 and 1: {invalid_weights}")
        return self

    def _validate_acyclic_parents(self) -> None:
        parents = {topic.topic_key: topic.parent_topic_key for topic in self.topics}
        for topic_key in parents:
            visited: set[str] = set()
            current: str | None = topic_key
            while current is not None:
                if current in visited:
                    raise ValueError(f"topic parent cycle includes: {current}")
                visited.add(current)
                current = parents[current]


class TopicMatch(TaxonomyModel):
    topic_key: str
    user_weight: float = Field(ge=0, le=1)


class TaxonomySeedResult(TaxonomyModel):
    taxonomy_version: str
    created: int
    updated: int


def load_default_taxonomy() -> TopicTaxonomy:
    """Load and validate the package-local phase-one taxonomy."""
    resource = files("app.catalog.data").joinpath("topic_taxonomy.v1.json")
    try:
        return TopicTaxonomy.model_validate_json(resource.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise TaxonomyError(f"could not load controlled topic taxonomy: {error}") from error


class TopicTaxonomyService:
    """Seed controlled topics and expose deterministic connector mappings."""

    def __init__(self, repository: TopicRepository, taxonomy: TopicTaxonomy) -> None:
        self._repository = repository
        self._taxonomy = taxonomy
        self._definitions = {topic.topic_key: topic for topic in taxonomy.topics}
        self._mappings = {
            (mapping.source_key.strip().casefold(), mapping.source_category.strip().casefold()): (
                mapping.topic_keys
            )
            for mapping in taxonomy.source_category_mappings
        }

    @property
    def version(self) -> str:
        return self._taxonomy.taxonomy_version

    def seed(self) -> TaxonomySeedResult:
        """Idempotently seed topics; callers own the surrounding transaction."""
        created = 0
        updated = 0
        persisted: dict[str, Topic] = {}
        for definition in self._ordered_definitions():
            parent = (
                None
                if definition.parent_topic_key is None
                else persisted[definition.parent_topic_key].id
            )
            result = self._repository.upsert(
                Topic(
                    id=f"topic:{definition.topic_key}",
                    topic_key=definition.topic_key,
                    display_name=definition.display_name,
                    parent_topic_id=parent,
                    description=definition.description,
                    active=definition.active,
                )
            )
            persisted[definition.topic_key] = result.entity
            created += int(result.created)
            updated += int(not result.created)
        return TaxonomySeedResult(
            taxonomy_version=self.version,
            created=created,
            updated=updated,
        )

    def map_source_category(self, source_key: str, source_category: str) -> tuple[TopicMatch, ...]:
        keys = self._mappings.get(
            (source_key.strip().casefold(), source_category.strip().casefold()),
            ("unknown",),
        )
        return tuple(
            TopicMatch(topic_key=key, user_weight=self._taxonomy.user_weights[key]) for key in keys
        )

    def user_weight(self, topic_key: str) -> float:
        try:
            return self._taxonomy.user_weights[topic_key]
        except KeyError as error:
            raise TaxonomyError(f"unknown controlled topic: {topic_key}") from error

    def _ordered_definitions(self) -> tuple[TopicDefinition, ...]:
        ordered: list[TopicDefinition] = []
        visited: set[str] = set()

        def add(topic_key: str) -> None:
            if topic_key in visited:
                return
            definition = self._definitions[topic_key]
            if definition.parent_topic_key is not None:
                add(definition.parent_topic_key)
            visited.add(topic_key)
            ordered.append(definition)

        for definition in self._taxonomy.topics:
            add(definition.topic_key)
        return tuple(ordered)
