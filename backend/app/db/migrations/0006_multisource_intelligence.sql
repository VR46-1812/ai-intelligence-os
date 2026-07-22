CREATE TABLE IF NOT EXISTS source_artifacts (
  id TEXT PRIMARY KEY,
  source_record_id TEXT NOT NULL UNIQUE REFERENCES source_records(id) ON DELETE CASCADE,
  source_key TEXT NOT NULL,
  upstream_id TEXT NOT NULL,
  artifact_type TEXT NOT NULL CHECK (artifact_type IN ('paper','repository','release','model','dataset','space','official_post')),
  title TEXT NOT NULL,
  summary TEXT,
  canonical_url TEXT NOT NULL,
  content_class TEXT NOT NULL CHECK (content_class IN ('fact','interpretation','community_reaction','commercial_hypothesis')),
  authority REAL NOT NULL CHECK (authority BETWEEN 0 AND 1),
  freshness REAL NOT NULL CHECK (freshness BETWEEN 0 AND 1),
  novelty REAL NOT NULL CHECK (novelty BETWEEN 0 AND 1),
  published_at TEXT,
  updated_at TEXT,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(source_key, upstream_id)
);

CREATE TABLE IF NOT EXISTS linked_events (
  id TEXT PRIMARY KEY,
  canonical_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  primary_work_id TEXT REFERENCES works(id) ON DELETE SET NULL,
  occurred_at TEXT,
  corroboration REAL NOT NULL CHECK (corroboration BETWEEN 0 AND 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS linked_event_artifacts (
  event_id TEXT NOT NULL REFERENCES linked_events(id) ON DELETE CASCADE,
  artifact_id TEXT NOT NULL REFERENCES source_artifacts(id) ON DELETE CASCADE,
  relationship TEXT NOT NULL CHECK (relationship IN ('primary_research','official_repository','official_model','official_announcement','release','community_reference')),
  resolution_basis TEXT NOT NULL CHECK (resolution_basis IN ('external_id','explicit_url','canonical_work','manual')),
  PRIMARY KEY(event_id, artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_source_artifacts_source_updated
ON source_artifacts(source_key, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_linked_events_work ON linked_events(primary_work_id, updated_at DESC);
