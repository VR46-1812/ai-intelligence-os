# AI Intelligence OS V1 Release Runbook

## Supported release boundary

V1 is a local-only Windows application using arXiv, SQLite/filesystem storage,
React, and the installed Ollama `qwen3:4b` model. It does not require a paid API,
cloud service, Docker, authentication, another research source, or another model.

## Operator commands

Run all commands from `D:\Rujay\ai-intelligence-os`.

```powershell
.\scripts\start.ps1
.\scripts\run-daily.ps1
.\scripts\release-verify.ps1
.\scripts\stop.ps1
```

`start.ps1` starts the backend and frontend as workspace-managed local processes.
The UI is `http://127.0.0.1:5173`; API documentation is
`http://127.0.0.1:8000/docs`. `run-daily.ps1` runs the same bounded production
pipeline used by the scheduler and Run Now controls.

## Daily acceptance path

One run performs, in order:

1. One bounded arXiv page containing at most five records.
2. Acquisition/extraction of at most five required PDFs.
3. Deterministic reranking of the available catalog.
4. Generation or reuse of one fast brief.
5. Sequential generation, cached reuse, or failed-stage resume for at most two
   evidence-bearing papers ordered by deep-dive priority.
6. Skeptic and citation publication gates.
7. Idempotent final daily-report persistence and bounded retention cleanup.

The run fails safely before report publication when a required brief or deep dive
does not pass its gates. Run Now resumes persisted stages. Successful inputs reuse
the existing analysis and the report date remains unique. Ollama calls share one
semaphore and send `keep_alive=0`, allowing `qwen3:4b` to unload after each call.

## Backup and restore drill

Create a manifest-verified online backup:

```powershell
.\scripts\backup-database.ps1 --destination backups\v1-drill.db
```

This creates `v1-drill.db` and `v1-drill.db.manifest.json` under `data/backups`.
Stop the application before restoring the active database:

```powershell
.\scripts\stop.ps1
.\scripts\restore-database.ps1 backups\v1-drill.db --overwrite
```

Restore validates the SHA-256 digest, byte size, SQLite integrity, and ordered
migration history before atomically replacing the destination. The automated
test suite performs the same round trip against temporary data.

## Failure recovery

- An overlapping daily run is rejected; wait for or inspect the current run.
- An interrupted queued/running daily run is marked failed on startup and may be
  retried from Today or System.
- A deep dive resumes its first failed/pending stage; successful stages and the
  final analysis are reused.
- Unsupported claims, unknown evidence IDs, citation coverage below 90%, or an
  unresolved critical skeptic finding block publication.
- Public APIs and UI show safe details only. Local logs under `.cache` retain
  diagnostic context without returning paths, prompts, raw payloads, or SQL.

## Human review and evaluation

The versioned 50-example golden set measures completeness, citation coverage,
repetition, unsupported-claim rejection, precision@10, and nDCG@10. Twelve
independently specified operator-review cases are stored in
`backend/app/intelligence/human_review_v1.json` now contains twenty independently
specified Weekend Beta cases and is exposed at
`GET /evaluations/human-review/v1`. Reviewers record pass/fail for each case and
retain notes outside the immutable fixture.

## V1 release checklist

- [x] arXiv sync is limited to five records per daily run.
- [x] required document processing is limited to five.
- [x] deterministic ranking remains authoritative and inspectable.
- [x] one fast brief and up to two priority deep dives run sequentially.
- [x] deep-dive stages resume and successful analyses/report dates are idempotent.
- [x] major factual claims require stored evidence; skeptic critical findings block.
- [x] Today, Explore, Topics, Opportunities, Report, and System have explicit states.
- [x] SQLite online backup includes a manifest and passes a tested restore drill.
- [x] one root command starts V1, one runs the daily pipeline, and one verifies release.
- [x] backend/frontend/installed-Chrome suites and dependency audits are release gates.
- [x] RAM, VRAM, concurrency, download, token, retention, and daily-work limits remain enforced.

## Known non-blocking limitations

The stable V1 baseline is single-user and local-first. V1.1 and Weekend Beta
source/agent behavior is documented separately in `docs/V1_1_RELEASE.md` and
`docs/WEEKEND_BETA.md`. Commercial hypotheses require human market validation.
The in-process scheduler runs only while the backend is running; Windows Task
Scheduler integration is an operator choice for a later release.
