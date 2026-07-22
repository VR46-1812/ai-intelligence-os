CREATE TABLE agent_executions (
  id TEXT PRIMARY KEY,
  pipeline_run_id TEXT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL,
  agent_version TEXT NOT NULL,
  stage_order INTEGER NOT NULL CHECK(stage_order BETWEEN 1 AND 14),
  responsibility TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','skipped')),
  idempotency_key TEXT NOT NULL UNIQUE,
  attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 2),
  input_json TEXT NOT NULL DEFAULT '{}',
  output_json TEXT,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  provenance_refs_json TEXT NOT NULL DEFAULT '[]',
  metrics_json TEXT NOT NULL DEFAULT '{}',
  safe_failure_reason TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(pipeline_run_id, agent_id)
);

CREATE INDEX idx_agent_executions_run_order
ON agent_executions(pipeline_run_id, stage_order);

ALTER TABLE linked_event_artifacts ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0
  CHECK(confidence >= 0 AND confidence <= 1);
ALTER TABLE linked_event_artifacts ADD COLUMN matching_evidence_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE linked_event_artifacts ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1));
ALTER TABLE linked_event_artifacts ADD COLUMN corrected_at TEXT;
ALTER TABLE linked_event_artifacts ADD COLUMN correction_reason TEXT;

CREATE TABLE watchlist_inputs (
  id TEXT PRIMARY KEY,
  source_kind TEXT NOT NULL CHECK(source_kind IN
    ('youtube','reddit','medium','substack','rss','github','x_export')),
  label TEXT NOT NULL,
  location TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(source_kind, location)
);
