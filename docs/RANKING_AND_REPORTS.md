# Ranking Formula and Report Schemas

## 1. Ranking design

Ranking is deterministic arithmetic over persisted features. Local models may produce bounded semantic feature estimates, but they never return the final score.

All features are normalized to `[0,1]`, stored with evidence/method, and calculated as-of a timestamp.

## 2. Technical value score

\[
T = 100(0.20R + 0.15N + 0.15E + 0.12P + 0.12I + 0.10M + 0.08Q + 0.08F)
\]

| Feature | Weight | Meaning |
|---|---:|---|
| `R` Relevance | 0.20 | Match to explicit technical priorities |
| `N` Novelty | 0.15 | Material difference from known prior work |
| `E` Evidence strength | 0.15 | Evaluation quality, baselines, ablations, transparency |
| `P` Reproducibility | 0.12 | Code, model/data access, instructions, feasible dependencies |
| `I` Engineering impact | 0.12 | Likely effect on real system capability/cost/reliability |
| `M` Method depth | 0.10 | Substantive technique rather than announcement/packaging |
| `Q` Source quality | 0.08 | Trust tier and publication/review context |
| `F` Freshness | 0.08 | Time-decayed recency, boosted for meaningful revisions |

### Technical penalties

Subtract after the weighted score:

- `-20` confirmed retraction, fabricated identity, or invalid source.
- `-12` critical unresolved benchmark integrity concern.
- `-8` marketing-only claim with no technical evidence.
- `-5` no accessible primary source.
- `-3` duplicate coverage with no material revision.

Clamp result to `[0,100]`.

## 3. Commercial opportunity score

\[
C = 100(0.18U + 0.16W + 0.14D + 0.13B + 0.12S + 0.10A + 0.09G + 0.08X)
\]

| Feature | Weight | Meaning |
|---|---:|---|
| `U` Urgency | 0.18 | Pain occurs frequently and has visible cost/risk |
| `W` Willingness | 0.16 | Identifiable buyer plausibly pays for outcome |
| `D` Demonstrability | 0.14 | Convincing demo can be built with available capability |
| `B` Build feasibility | 0.13 | Pilot feasible with current hardware/team/tooling |
| `S` Sales speed | 0.12 | Reachable buyer and short pilot decision cycle |
| `A` Advantage | 0.10 | Technique creates meaningful differentiation |
| `G` Generalizability | 0.09 | Repeatable across several customers/documents/workflows |
| `X` Existing-project fit | 0.08 | Reuse in RentAssure, bid intelligence, school AI, etc. |

Commercial conclusions are hypotheses unless linked to actual market/customer evidence. Academic novelty is not required for a strong commercial score.

## 4. Deep-dive priority

\[
D = 0.55T + 0.30C + 15K - 20A_c - 10H
\]

Where:

- `K = 1` for a critical tracked-topic match, otherwise `0`.
- `A_c = 1` when already analyzed at the same material version, otherwise `0`.
- `H = 1` when estimated analysis cost exceeds the daily remaining budget, otherwise `0`.

Rules:

- Candidate must have at least one Tier A primary source.
- Candidate with source-integrity penalty cannot auto-enter deep analysis.
- Daily automatic deep dives are the highest-scoring two, with a third only when `D >= 78` and resource budget permits.

## 5. Feature computation

### Deterministic features

- Freshness from timestamps using configurable exponential decay.
- Source quality from trust tier and publication state.
- Code/data/license/test availability from structured metadata.
- Existing analysis/version penalty from database state.
- Topic match from explicit taxonomy and user weights.

### Model-assisted features

- Novelty.
- Method depth.
- Engineering impact.
- Urgency and advantage hypotheses.

Model-assisted feature contract:

```json
{
  "feature": "novelty",
  "value": 0.72,
  "confidence": 0.61,
  "rationale": "Specific comparison against retrieved prior works.",
  "evidence_ids": ["ev_..."],
  "unknowns": ["No comparison against method X was found"]
}
```

If confidence is below `0.55`, shrink the value toward neutral `0.5`:

\[
v' = 0.5 + confidence(v - 0.5)
\]

## 6. Ranking calibration

Create at least 100 labelled candidates spanning relevant, irrelevant, incremental, influential, reproducible, unreproducible, and commercially promising cases. Measure precision@10 and nDCG@10 separately for technical and commercial lists. Change weights only through a new version and compare both automatic metrics and user feedback.

## 7. Report types

### Daily intelligence report

- Pipeline summary.
- Top developments.
- Deep dives completed.
- Important revisions/releases.
- Commercial opportunities.
- Suggested learning focus.
- Failures and coverage gaps.

### Fast brief

- Change in one sentence.
- Problem.
- Claimed contribution.
- Evidence available.
- Limitations detected.
- Code/data availability.
- Technical relevance.
- Commercial relevance.
- Recommended action.

### Deep dive

- Identity and status.
- Executive significance.
- Prior problem/context.
- Method from first principles.
- Architecture/data flow.
- Algorithms/equations/assumptions.
- Training/inference/data requirements.
- Evaluation and benchmark critique.
- Limitations and conflicting evidence.
- Code and reproducibility.
- Target-hardware reproduction plan.
- Production integration.
- Commercial hypothesis.
- Prerequisite learning path.
- Skeptic findings.
- Claims and evidence map.

## 8. Claim contract

Every report claim is one of:

- `fact`: directly supported by a source.
- `interpretation`: reasoned reading of supported facts.
- `recommendation`: proposed action.
- `hypothesis`: unverified technical or commercial proposition.

Each major/critical fact requires evidence. Interpretations must link the facts used. Recommendations and hypotheses must not be phrased as established facts.

## 9. Verification gates

A deep dive can become `verified` only if:

- Required sections exist.
- JSON validates.
- No invented URLs.
- All major/critical factual claims have evidence.
- Citation coverage >= 0.90.
- No unresolved critical skeptic finding.
- Paper status and repository relationship are explicit.
- Confidence is section-specific rather than a decorative global score.

## 10. UI presentation rules

- Show technical and commercial scores separately.
- Show score component breakdown on demand.
- Display `preprint`, `accepted`, `published`, and `unknown` clearly.
- Evidence links open the exact page/section/span when possible.
- Use warning styling for conflicts and unsupported hypotheses.
- Never hide reviewer criticism beneath the executive summary.
