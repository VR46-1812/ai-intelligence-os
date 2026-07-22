# Source Connector Contracts

## 1. Boundary

A connector performs upstream communication and source-specific normalization only. It must not rank, summarize, embed, or call an LLM.

Pipeline boundary:

`Source API/RSS -> RawSourceRecord -> NormalizedRecord -> Catalog identity service`

## 2. Python protocol

```python
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

TrustTier = Literal["A", "B", "C", "D"]

@dataclass(frozen=True)
class FetchWindow:
    since: datetime
    until: datetime
    cursor: dict[str, Any] | None
    page_size: int

@dataclass(frozen=True)
class RawSourceRecord:
    source_key: str
    upstream_id: str
    upstream_version: str | None
    canonical_url: str
    observed_at: datetime
    published_at: datetime | None
    updated_at: datetime | None
    media_type: str
    payload: bytes
    response_metadata: dict[str, Any]

@dataclass(frozen=True)
class NormalizedIdentity:
    id_type: Literal["doi", "arxiv", "openreview", "github", "url", "other"]
    raw_value: str
    normalized_value: str

@dataclass(frozen=True)
class NormalizedAuthor:
    display_name: str
    normalized_name: str
    order: int
    orcid: str | None = None
    affiliation: str | None = None

@dataclass(frozen=True)
class NormalizedRecord:
    source_key: str
    upstream_id: str
    upstream_version: str | None
    work_type: str
    title: str
    normalized_title: str
    abstract: str | None
    canonical_url: str
    publication_status: str
    published_at: datetime | None
    updated_at: datetime | None
    identities: tuple[NormalizedIdentity, ...]
    authors: tuple[NormalizedAuthor, ...]
    source_topics: tuple[str, ...]
    document_urls: tuple[str, ...]
    repository_urls: tuple[str, ...]
    license_hint: str | None
    extra: dict[str, Any]

@dataclass(frozen=True)
class ConnectorPage:
    records: tuple[RawSourceRecord, ...]
    next_cursor: dict[str, Any] | None
    exhausted: bool

class SourceConnector(Protocol):
    key: str
    trust_tier: TrustTier
    connector_version: str

    async def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]: ...
    def normalize(self, record: RawSourceRecord) -> NormalizedRecord: ...
    def validate(self, record: NormalizedRecord) -> list[str]: ...
```

## 3. Connector guarantees

Every connector must:

- Generate a stable `upstream_id`.
- Emit the source's canonical public URL.
- Preserve raw bytes before normalization.
- Normalize timestamps to UTC without inventing missing values.
- Separate upstream version from stable identity.
- Respect source rate limits and terms.
- Use conditional requests (`ETag`, `If-Modified-Since`) where supported.
- Limit response size and validate media types.
- Return structured errors rather than arbitrary strings.
- Be testable entirely from fixtures.

Every connector must not:

- Follow arbitrary URLs found inside source content.
- Execute scripts or repository content.
- Treat HTML/PDF instructions as agent instructions.
- Merge catalog entities directly.
- Infer peer-review status from venue-looking text alone.
- Convert a missing field into a confident value.

## 4. Standard error taxonomy

| Code | Retry? | Meaning |
|---|---:|---|
| `AUTH_REQUIRED` | No | Credential required or invalid |
| `RATE_LIMITED` | Yes | 429 or documented quota response |
| `UPSTREAM_5XX` | Yes | Server-side transient failure |
| `NETWORK_TIMEOUT` | Yes | Connect/read timeout |
| `INVALID_RESPONSE` | Usually no | Payload cannot be parsed |
| `SCHEMA_DRIFT` | No | Previously required upstream fields changed |
| `TERMS_BLOCKED` | No | Collection is not permitted |
| `CONTENT_TOO_LARGE` | No | Configured safety limit exceeded |
| `UNSUPPORTED_MEDIA` | No | Unexpected content type |
| `NORMALIZATION_FAILED` | No | Individual record cannot normalize |

Retry policy: maximum three attempts, exponential delay with jitter, connector-specific minimum interval, and no retry for deterministic 4xx failures except 408/429.

## 5. Cursor/checkpoint contract

The ingestion service commits a cursor only after all records on the corresponding page are durably captured. Normalization may happen after raw capture. A safety overlap window re-reads recent records; hashes and unique keys make this idempotent.

Cursor JSON must include:

```json
{
  "schema_version": 1,
  "position": "source-specific opaque value",
  "window_end": "2026-07-17T00:00:00Z",
  "last_upstream_id": "optional diagnostic value"
}
```

## 6. arXiv connector v1

### Scope

Categories: `cs.AI`, `cs.LG`, `cs.CL`, `cs.CV`, `cs.RO`, `stat.ML`, configurable in source data.

### Identity

- Stable identity: lowercase arXiv ID without `vN`.
- Version: parsed `vN` when available.
- Canonical URL: `https://arxiv.org/abs/{stable_id}`.
- DOI: include only when supplied and syntactically valid.

### Requirements

- Use the official API/RSS behavior and minimum request interval of at least three seconds.
- Single connection for the legacy API.
- Paginate conservatively.
- Normalize Atom namespaces explicitly.
- Preserve arXiv categories as source-topic evidence.
- Publication status is `preprint` unless stronger canonical evidence exists elsewhere.

### Fixtures

- Normal single-version record.
- Multiple authors and categories.
- DOI present.
- Revision record.
- Missing optional fields.
- Malformed entry isolated from valid entries.
- Rate-limit and 5xx responses.

## 7. OpenReview connector v1

### Scope

Current API v2 venues configured through allowlisted venue/group IDs. Legacy API support is not phase-one work.

### Identity

- Stable identity: forum ID where present; otherwise note/submission ID with a documented qualifier.
- Version: note revision number or modification timestamp.
- Canonical URL generated from the official OpenReview domain and stable ID.

### Requirements

- Capture venue, invitation/domain, submission content, decision where public, and public reviews/comments as distinct related raw records.
- Do not expose blind author identities.
- Do not treat submission as accepted without a public decision record.
- Reviews are evidence/commentary, not paper facts.
- Handle content fields whose values are wrapped in OpenReview's structured `{value: ...}` representation.

### Fixtures

- Submission without decision.
- Accepted submission.
- Rejected submission.
- Revised note.
- Public reviews/comments.
- Blind submission.
- Venue/schema variation.

## 8. GitHub enrichment connector v1

### Scope

GitHub is called only for repository URLs linked by canonical paper metadata, authors, official model cards, or explicit user input. Broad trend scraping is not required in phase one.

### Identity

- Normalize to lowercase host and owner/repository path.
- Remove `.git`, fragments, tracking queries, and trailing slash.
- Resolve redirects without accepting a different host unexpectedly.

### Captured metadata

- Repository owner/name and canonical URL.
- Description and topics.
- License API value and license file presence.
- Archived/fork state.
- Default branch.
- Latest push/commit timestamp.
- Releases and latest release time.
- README and dependency-manifest presence.
- Test/workflow directory presence.
- Link relationship evidence.

### Requirements

- Cache responses and use conditional requests.
- Operate within the available authenticated or unauthenticated free allowance.
- Do not clone by default.
- Do not execute repository content.
- Do not infer quality from stars alone.

## 9. Future connector definitions

V1.1 implements Hugging Face model/dataset/Space discovery and allowlisted
official RSS/Atom feeds through this same versioned contract. OpenAlex,
Semantic Scholar, and Crossref remain future adapters. Source-specific fields
remain in `extra` until promoted through an explicit schema decision.

## 10. Contract test suite

Every connector must pass:

1. Fixture parsing.
2. Stable identity snapshot.
3. Canonical URL snapshot.
4. Timestamp normalization.
5. Optional-field behavior.
6. Invalid-record isolation.
7. Pagination/cursor behavior.
8. Rate-limit behavior with virtual time.
9. Idempotent raw-record persistence.
10. Redaction test ensuring credentials never appear in payload/log snapshots.
