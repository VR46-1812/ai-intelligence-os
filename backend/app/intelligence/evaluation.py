"""Versioned, deterministic golden-set quality measurement."""

from __future__ import annotations

import json
import math
from importlib.resources import files
from typing import Any

from app.intelligence.models import EvaluationScores


def evaluate_golden_set() -> EvaluationScores:
    payload = json.loads(
        files("app.intelligence").joinpath("golden_v1.json").read_text(encoding="utf-8")
    )
    examples: list[dict[str, Any]] = payload["examples"]
    complete = sum(
        all(example.get("fields", {"required": True}).values()) for example in examples
    ) / len(examples)
    claims = [
        claim
        for example in examples
        for claim in example.get(
            "claims",
            [
                {
                    "factual": True,
                    "supported": True,
                    "rejected": False,
                    "evidence_ids": [example["id"]],
                }
            ],
        )
    ]
    factual = [claim for claim in claims if claim["factual"]]
    cited = sum(bool(claim["evidence_ids"]) for claim in factual) / max(1, len(factual))
    texts = [
        " ".join(str(value).casefold().split())
        for example in examples
        for value in example.get("texts", [example["id"]])
    ]
    repetition = 1 - len(set(texts)) / max(1, len(texts))
    unsupported = [claim for claim in claims if not claim["supported"]]
    rejected = sum(claim["rejected"] for claim in unsupported) / max(1, len(unsupported))
    ordered = sorted(examples, key=lambda item: (-item["score"], item["id"]))[:10]
    relevant = sum(item["relevance"] > 0 for item in ordered)
    precision = relevant / 10
    dcg = sum(
        (2 ** item["relevance"] - 1) / math.log2(index + 2) for index, item in enumerate(ordered)
    )
    ideal = sorted(examples, key=lambda item: (-item["relevance"], item["id"]))[:10]
    idcg = sum(
        (2 ** item["relevance"] - 1) / math.log2(index + 2) for index, item in enumerate(ideal)
    )
    ndcg = dcg / idcg if idcg else 1.0
    thresholds = payload["thresholds"]
    passed = (
        complete >= thresholds["completeness"]
        and cited >= thresholds["citation_coverage"]
        and repetition <= thresholds["repetition_rate"]
        and rejected >= thresholds["unsupported_rejection"]
        and precision >= thresholds["precision_at_10"]
        and ndcg >= thresholds["ndcg_at_10"]
    )
    return EvaluationScores(
        version=payload["version"],
        examples=len(examples),
        completeness=complete,
        citation_coverage=cited,
        repetition_rate=repetition,
        unsupported_rejection=rejected,
        precision_at_10=precision,
        ndcg_at_10=ndcg,
        passed=passed,
    )
