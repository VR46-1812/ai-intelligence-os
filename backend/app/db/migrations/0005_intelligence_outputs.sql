CREATE TABLE IF NOT EXISTS deep_dive_jobs (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  work_version_id TEXT NOT NULL REFERENCES work_versions(id),
  input_fingerprint TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','rejected')),
  analysis_run_id TEXT REFERENCES analysis_runs(id) ON DELETE SET NULL,
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deep_dive_stages (
  job_id TEXT NOT NULL REFERENCES deep_dive_jobs(id) ON DELETE CASCADE,
  stage_key TEXT NOT NULL CHECK (stage_key IN ('extract','analyze','skeptic_check','verify_citations','publish')),
  stage_order INTEGER NOT NULL CHECK (stage_order BETWEEN 1 AND 5),
  status TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed','rejected')),
  input_fingerprint TEXT NOT NULL,
  output_json TEXT,
  error_code TEXT,
  started_at TEXT,
  completed_at TEXT,
  PRIMARY KEY(job_id, stage_key)
);

CREATE TABLE IF NOT EXISTS daily_reports (
  report_date TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL CHECK (schema_version='1.0'),
  pipeline_run_id TEXT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  input_fingerprint TEXT NOT NULL,
  report_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ranking_model_signals (
  work_id TEXT PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
  analysis_run_id TEXT REFERENCES analysis_runs(id) ON DELETE SET NULL,
  input_fingerprint TEXT NOT NULL,
  novelty REAL NOT NULL CHECK (novelty BETWEEN 0 AND 1),
  method_depth REAL NOT NULL CHECK (method_depth BETWEEN 0 AND 1),
  impact REAL NOT NULL CHECK (impact BETWEEN 0 AND 1),
  opportunity REAL NOT NULL CHECK (opportunity BETWEEN 0 AND 1),
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  fallback INTEGER NOT NULL CHECK (fallback IN (0,1)),
  evidence_ids_json TEXT NOT NULL,
  rationale TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deep_dive_jobs_work ON deep_dive_jobs(work_id, updated_at DESC);
