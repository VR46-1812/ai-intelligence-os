# AI Intelligence OS V1.1 Multi-Source Operations

## Release boundary

V1.1 preserves the local arXiv V1 baseline and adds free public metadata from:

- OpenReview API v2 submissions for configured venue IDs.
- GitHub API enrichment only for explicitly linked repositories.
- Hugging Face Hub models, datasets, and Spaces.
- HTTPS RSS/Atom feeds from an operator allowlist of official organizations.

This document describes the stable V1.1 boundary. Weekend Beta adds bounded
public feeds and user-supplied exports under the stricter policy documented in
`docs/WEEKEND_BETA.md`. Paid APIs, cloud services, authentication, Docker,
repository cloning, and downloaded-code execution remain excluded.

## Operator flow

From `D:\Rujay\ai-intelligence-os`:

```powershell
.\scripts\start.ps1
.\scripts\run-daily.ps1
.\scripts\release-verify.ps1
```

The daily run fetches at most five records per enabled source. HTTP work retains
the existing three-request concurrency ceiling, bounded retry/backoff, timeouts,
response-size checks, immutable raw payloads, and independent checkpoints.
A failed source is marked failed/degraded without discarding another source's
committed page. Ollama calls remain sequential and unload after generation.

Manual metadata-only discovery is also available at:

```text
POST http://127.0.0.1:8000/multi-source/sync
{"maximum_records":5,"lookback_hours":168}
```

Inspect connector versions/checkpoints at `GET /sources/registry` and linked
developments at `GET /events?limit=20&offset=0&source=github`.

## Evidence and linking policy

Cross-source resolution uses only exact DOI/arXiv/OpenReview identities and
explicit canonical repository URLs. It does not use an LLM or fuzzy auto-merge.
A paper, official repository, tagged Hub asset, and official announcement share
one linked event when those identifiers agree. Re-ingestion updates the existing
artifact/event and sets novelty to zero rather than creating duplicate cards.

Every artifact is labeled as fact, interpretation, community reaction, or
commercial hypothesis. OpenReview paper/decision evidence is primary research.
GitHub and Hub fields are objective implementation/model metadata; existence,
stars, downloads, or promotional language do not establish correctness.
Official posts are announcement evidence, not substitutes for paper evidence.
Deterministic catalog ranking remains authoritative.

## Configuration and failure recovery

Source enablement, venue IDs, feed allowlists, optional GitHub token, retry
policy, and resource ceilings are environment-backed and typed. Tokens are sent
only to GitHub, are never persisted, and remain redacted from settings/logs.

On a source failure, inspect System/source registry, correct access or an
allowlist entry, and rerun. The source resumes from its own durable checkpoint.
Other source artifacts, analyses, daily reports, and backups remain usable.
