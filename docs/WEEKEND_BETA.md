# AI Intelligence OS Weekend Beta

## Operating boundary

Weekend Beta remains a local modular monolith. It uses SQLite, local files and
only `qwen3:4b` through the existing one-generation semaphore. Deterministic
normalization, trust classification, linking, ranking, citation validation,
idempotency and retention remain deterministic.

The persisted agent graph is strictly ordered: orchestrator, source scout,
curator, event linker, trend/ranking, evidence, technical analyst, skeptic,
learning, commercial opportunity, India market, personal relevance, daily
editor and operations watchtower. Every run records queued, running, succeeded,
failed or skipped state, inputs, outputs, evidence/provenance references,
duration, evaluation metrics, bounded retry attempts and safe UI failures.
Retry resumes the failed run and reuses successful agent checkpoints.

## Source access and trust

- arXiv, OpenReview, GitHub, Hugging Face and official RSS retain their V1.1 behavior.
- GitHub discovery uses configured bounded search queries and repository watchlists.
- YouTube uses configured official channel Atom feeds. Only transcript URLs
  explicitly published in feed metadata are retained; the beta does not bypass
  YouTube controls or use an unofficial transcript scraper.
- Reddit uses public RSS or a future operator-provided permitted API integration.
- Medium and Substack use configured public RSS feeds only.
- Researcher/company feeds are an explicit HTTPS allowlist.
- X accepts at most five user-supplied export records through `POST /sources/x-import`.
  It does not scrape X and no paid API is required.

Community and promotional records are discovery signals. They cannot verify a
technical fact without matching primary evidence. Resolution requires an exact
DOI/arXiv/OpenReview identity or an explicit canonical repository URL. Similar
titles never auto-merge. Operators can deactivate a bad association with
`POST /events/{event_id}/artifacts/{artifact_id}/unlink` and a required reason.

OpenReview API v2 is the supported path. The current public endpoint may return
a browser challenge (`ChallengeRequiredError`) from some networks. The connector
does not bypass it: OpenReview enters degraded state while every other source
continues. Retry from another permitted network or after OpenReview restores
public API access; no credential workaround is embedded.

## Commands

From `D:\Rujay\ai-intelligence-os`:

```powershell
.\scripts\start.ps1
.\scripts\run-daily.ps1
.\scripts\release-verify.ps1
```

The scheduler runs at 06:00 Asia/Kolkata, permits one active run, recovers a
recent missed run and performs retention only after prior stages succeed.
Startup creates a manifest-verified pre-migration backup before upgrading an
existing database. Agent status and retry are available under `/agents/status`
and `/agents/retry`; the System screen provides the same controls.

## Known beta limitations

Public-feed availability and completeness belong to upstream platforms.
Publisher-provided YouTube transcript links are uncommon, Reddit may throttle
anonymous RSS, and X live discovery requires an approved API that is not free;
therefore X is export-only. GitHub unauthenticated search has a lower public rate
limit. Commercial prices, Indian buyer demand and personal-project fit are
explicit hypotheses requiring human validation.
