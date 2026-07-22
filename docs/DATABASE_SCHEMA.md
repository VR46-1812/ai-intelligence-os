# Database Schema

## Design goals

- SQLite-compatible and lightweight.
- Idempotent ingestion and explicit version history.
- Evidence-level provenance.
- Replayable ranking and prompt/model results.
- FTS usable without loading an embedding model.
- Replaceable persistence through repository interfaces.

The executable baseline is in `contracts/schema.sql`. Use UTC ISO-8601 timestamps and application-generated UUIDv7/ULID-style text IDs. SQLite foreign keys must be enabled and WAL mode configured at connection initialization.

## Core entity relationships

| Parent | Child | Cardinality | Meaning |
|---|---|---:|---|
| `sources` | `source_records` | 1:N | raw upstream records |
| `works` | `work_versions` | 1:N | stable identity and revisions |
| `works` | `external_ids` | 1:N | DOI/arXiv/OpenReview identifiers |
| `source_records` | `source_artifacts` | 1:0..1 | normalized V1.1 source artifact with provenance |
| `works` | `linked_events` | 1:N | canonical cross-source development/event |
| `linked_events` | `source_artifacts` | N:M | typed evidence relationship and resolution basis |
| `works` | `work_authors` | 1:N | ordered authorship |
| `works` | `work_topics` | 1:N | controlled classifications |
| `work_versions` | `documents` | 1:N | downloaded representations |
| `documents` | `evidence_spans` | 1:N | citable passages |
| `works` | `ranking_results` | 1:N | replayable score versions |
| `works` | `analysis_runs` | 1:N | model analysis attempts |
| `analysis_runs` | `analysis_sections` | 1:N | structured report stages |
| `analysis_sections` | `claim_evidence` | N:M | claims linked to evidence |
| `works` | `repository_links` | 1:N | code relationships |
| `pipeline_runs` | `job_events` | 1:N | operational audit log |

## Key invariants

1. `works` is stable; changing paper content creates `work_versions`.
2. An external identity is unique per identifier type and normalized value.
3. Raw source payload is retained by hash/path for replay and diagnosis.
4. A ranking result is immutable once written; changed weights create a new `ranking_profiles` version.
5. A verified analysis cannot contain a major claim with zero evidence links.
6. The same analysis input fingerprint plus prompt/model profile may be reused rather than regenerated.
7. User feedback is append-only; derived preferences are recomputed.

## Search

FTS5 indexes title, abstract, topic text, report summaries, and repository descriptions. Use an external-content or rebuildable FTS table rather than treating the index as authoritative data.

Vector storage is intentionally excluded from the baseline SQL. The `embeddings` table stores provider-independent metadata and external vector keys so either `sqlite-vec` or LanceDB can be adopted after an on-device benchmark.

## Migration policy

- Use numbered SQL migrations with checksum tracking.
- Never edit a migration already applied to a released database.
- Back up the SQLite file before destructive migrations.
- Migrations must be tested against an empty database and the previous release fixture.
- Store schema version in `schema_migrations`.
