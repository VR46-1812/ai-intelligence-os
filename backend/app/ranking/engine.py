"""Documented deterministic ranking arithmetic over persisted local features."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.catalog.identity import new_ulid
from app.catalog.taxonomy import TopicTaxonomy
from app.db import transaction

TECHNICAL_WEIGHTS = {
    "R": 0.20,
    "N": 0.15,
    "E": 0.15,
    "P": 0.12,
    "I": 0.12,
    "M": 0.10,
    "Q": 0.08,
    "F": 0.08,
}
COMMERCIAL_WEIGHTS = {
    "U": 0.18,
    "W": 0.16,
    "D": 0.14,
    "B": 0.13,
    "S": 0.12,
    "A": 0.10,
    "G": 0.09,
    "X": 0.08,
}


class FeatureSnapshot(TypedDict):
    technical: dict[str, float]
    commercial: dict[str, float]
    technical_penalty: int
    critical_topic: int
    methods: dict[str, str]
    as_of: str


class RankingSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    profile_id: str
    profile_version: int
    works_ranked: int = Field(ge=0)
    results_created: int = Field(ge=0)
    results_reused: int = Field(ge=0)


class DeterministicRankingEngine:
    """Compute exact documented formulas; unknown semantic features remain neutral."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        taxonomy: TopicTaxonomy,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = new_ulid,
    ) -> None:
        self._connection = connection
        self._taxonomy = taxonomy
        self._clock = clock
        self._id_factory = id_factory

    def rank_catalog(self, *, limit: int = 100) -> RankingSummary:
        if not 1 <= limit <= 100:
            raise ValueError("ranking limit must be between 1 and 100")
        now = self._clock().astimezone(UTC)
        rows = self._connection.execute(
            """SELECT w.id, w.publication_status, w.first_published_at,
              s.trust_tier,
              EXISTS(SELECT 1 FROM documents d JOIN work_versions dv ON dv.id=d.work_version_id
                WHERE dv.work_id=w.id AND d.parse_status IN ('parsed','partial')) has_document,
              EXISTS(SELECT 1 FROM external_ids x WHERE x.work_id=w.id
                AND x.id_type='github') has_code,
              EXISTS(SELECT 1 FROM analysis_runs a WHERE a.work_id=w.id
                AND a.status='succeeded') analyzed
            FROM works w
            JOIN work_versions v ON v.id=w.current_version_id
            JOIN source_records sr ON sr.id=v.source_record_id
            JOIN sources s ON s.id=sr.source_id
            WHERE w.work_type='paper'
              AND w.lifecycle_state NOT IN ('failed','rejected','superseded')
            ORDER BY COALESCE(w.first_published_at,w.created_at) DESC, w.id LIMIT ?""",
            (limit,),
        ).fetchall()
        with transaction(self._connection):
            profile_id, profile_version = self._create_profile(now)
            created = 0
            for row in rows:
                features = self._features(row, now)
                technical_components = {
                    key: round(value * TECHNICAL_WEIGHTS[key] * 100, 4)
                    for key, value in features["technical"].items()
                }
                technical = self._clamp(
                    sum(technical_components.values()) - features["technical_penalty"]
                )
                commercial_components = {
                    key: round(value * COMMERCIAL_WEIGHTS[key] * 100, 4)
                    for key, value in features["commercial"].items()
                }
                commercial = self._clamp(sum(commercial_components.values()))
                deep = self._clamp(
                    0.55 * technical
                    + 0.30 * commercial
                    + 15 * features["critical_topic"]
                    - 20 * int(row["analyzed"])
                )
                created += self._insert_result(
                    profile_id,
                    str(row["id"]),
                    "technical",
                    technical,
                    technical_components,
                    features,
                    now,
                )
                created += self._insert_result(
                    profile_id,
                    str(row["id"]),
                    "commercial",
                    commercial,
                    commercial_components,
                    features,
                    now,
                )
                created += self._insert_result(
                    profile_id,
                    str(row["id"]),
                    "deep_dive_priority",
                    deep,
                    {
                        "technical": technical,
                        "commercial": commercial,
                        "critical_topic_bonus": 15 * features["critical_topic"],
                        "already_analyzed_penalty": 20 * int(row["analyzed"]),
                    },
                    features,
                    now,
                )
        expected = len(rows) * 3
        return RankingSummary(
            profile_id=profile_id,
            profile_version=profile_version,
            works_ranked=len(rows),
            results_created=created,
            results_reused=expected - created,
        )

    def _features(self, row: sqlite3.Row, now: datetime) -> FeatureSnapshot:
        topic_rows = self._connection.execute(
            """SELECT t.topic_key FROM work_topics wt JOIN topics t ON t.id=wt.topic_id
            WHERE wt.work_id=? AND t.active=1""",
            (row["id"],),
        ).fetchall()
        topic_weights = self._taxonomy.user_weights
        relevance = max(
            (topic_weights.get(str(topic[0]), 0.0) for topic in topic_rows), default=0.0
        )
        published = self._parse_time(row["first_published_at"])
        age_days = max(0.0, (now - published).total_seconds() / 86400) if published else 365.0
        freshness = math.exp(-math.log(2) * age_days / 30.0)
        source_quality = {"A": 1.0, "B": 0.75, "C": 0.5, "D": 0.25}.get(str(row["trust_tier"]), 0.5)
        if str(row["publication_status"]) == "preprint":
            source_quality *= 0.9
        has_document = bool(row["has_document"])
        has_code = bool(row["has_code"])
        technical = {
            "R": relevance,
            "N": 0.5,
            "E": 0.65 if has_document else 0.25,
            "P": 0.9 if has_code and has_document else (0.55 if has_document else 0.25),
            "I": 0.5,
            "M": 0.5,
            "Q": source_quality,
            "F": freshness,
        }
        commercial = {key: 0.5 for key in COMMERCIAL_WEIGHTS}
        commercial["B"] = 0.6 if has_document else 0.45
        commercial["X"] = relevance * 0.5
        return {
            "technical": technical,
            "commercial": commercial,
            "technical_penalty": 0,
            "critical_topic": int(relevance >= 0.95),
            "methods": {
                "freshness": "30-day exponential half-life",
                "source_quality": "trust tier adjusted for publication status",
                "relevance": f"taxonomy user weights {self._taxonomy.taxonomy_version}",
                "semantic_features": "neutral 0.5; no local model invoked",
                "evidence_strength": "primary-document availability proxy",
            },
            "as_of": now.isoformat(),
        }

    def _create_profile(self, now: datetime) -> tuple[str, int]:
        version_row = self._connection.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM ranking_profiles WHERE profile_key='default'"
        ).fetchone()
        version = 1 if version_row is None else int(version_row[0])
        profile_id = f"ranking-default-v{version}"
        self._connection.execute("UPDATE ranking_profiles SET active=0 WHERE active=1")
        self._connection.execute(
            """INSERT INTO ranking_profiles
            (id,profile_key,version,weights_json,normalization_json,active,created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (
                profile_id,
                "default",
                version,
                json.dumps(
                    {"technical": TECHNICAL_WEIGHTS, "commercial": COMMERCIAL_WEIGHTS},
                    sort_keys=True,
                ),
                json.dumps({"range": [0, 1], "freshness_half_life_days": 30}, sort_keys=True),
                1,
                now.isoformat(),
            ),
        )
        return profile_id, version

    def _insert_result(
        self,
        profile_id: str,
        work_id: str,
        kind: str,
        score: float,
        components: dict[str, float | int],
        features: FeatureSnapshot,
        now: datetime,
    ) -> int:
        cursor = self._connection.execute(
            """INSERT OR IGNORE INTO ranking_results
            (id,work_id,profile_id,score_kind,total_score,components_json,feature_snapshot_json,calculated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                self._id_factory(),
                work_id,
                profile_id,
                kind,
                round(score, 4),
                json.dumps(components, sort_keys=True, separators=(",", ":")),
                json.dumps(features, sort_keys=True, separators=(",", ":")),
                now.isoformat(),
            ),
        )
        return cursor.rowcount

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC)

    @staticmethod
    def _clamp(value: float) -> float:
        return min(100.0, max(0.0, value))
