PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  source_key TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  trust_tier TEXT NOT NULL CHECK (trust_tier IN ('A','B','C','D')),
  base_url TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  poll_interval_minutes INTEGER NOT NULL CHECK (poll_interval_minutes > 0),
  minimum_request_interval_ms INTEGER NOT NULL DEFAULT 0,
  connector_version TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}',
  cursor_json TEXT,
  health_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (health_status IN ('unknown','healthy','degraded','failed','disabled')),
  last_attempt_at TEXT,
  last_success_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_records (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id),
  upstream_id TEXT NOT NULL,
  upstream_version TEXT,
  canonical_url TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  raw_payload_path TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  published_at TEXT,
  updated_at_upstream TEXT,
  normalization_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (normalization_status IN ('pending','normalized','rejected','failed')),
  error_code TEXT,
  error_detail TEXT,
  UNIQUE(source_id, upstream_id, payload_sha256)
);

CREATE INDEX IF NOT EXISTS idx_source_records_observed
  ON source_records(source_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS works (
  id TEXT PRIMARY KEY,
  work_type TEXT NOT NULL
    CHECK (work_type IN ('paper','model','dataset','repository','article','release','other')),
  canonical_title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  abstract TEXT,
  language TEXT NOT NULL DEFAULT 'en',
  publication_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (publication_status IN ('unknown','preprint','submitted','accepted','published','withdrawn')),
  first_published_at TEXT,
  current_version_id TEXT,
  lifecycle_state TEXT NOT NULL DEFAULT 'discovered'
    CHECK (lifecycle_state IN ('discovered','normalized','shortlisted','acquired','parsed','briefed','analyzed','reviewed','verified','filtered','failed','rejected','superseded')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_works_state_date
  ON works(lifecycle_state, first_published_at DESC);
CREATE INDEX IF NOT EXISTS idx_works_normalized_title
  ON works(normalized_title);

CREATE TABLE IF NOT EXISTS external_ids (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  id_type TEXT NOT NULL CHECK (id_type IN ('doi','arxiv','openreview','github','huggingface','url','other')),
  normalized_value TEXT NOT NULL,
  raw_value TEXT NOT NULL,
  source_record_id TEXT REFERENCES source_records(id),
  created_at TEXT NOT NULL,
  UNIQUE(id_type, normalized_value)
);

CREATE TABLE IF NOT EXISTS work_versions (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  version_label TEXT NOT NULL,
  content_sha256 TEXT,
  title TEXT NOT NULL,
  abstract TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  source_record_id TEXT REFERENCES source_records(id),
  published_at TEXT,
  observed_at TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0,1)),
  UNIQUE(work_id, version_label)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_current_work_version
  ON work_versions(work_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS authors (
  id TEXT PRIMARY KEY,
  normalized_name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  orcid TEXT UNIQUE,
  affiliation_text TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_authors (
  work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  author_id TEXT NOT NULL REFERENCES authors(id),
  author_order INTEGER NOT NULL CHECK (author_order >= 1),
  is_corresponding INTEGER NOT NULL DEFAULT 0 CHECK (is_corresponding IN (0,1)),
  PRIMARY KEY(work_id, author_id),
  UNIQUE(work_id, author_order)
);

CREATE TABLE IF NOT EXISTS topics (
  id TEXT PRIMARY KEY,
  topic_key TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  parent_topic_id TEXT REFERENCES topics(id),
  description TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE IF NOT EXISTS work_topics (
  work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  topic_id TEXT NOT NULL REFERENCES topics(id),
  assignment_method TEXT NOT NULL CHECK (assignment_method IN ('source','rule','model','human')),
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  model_profile TEXT,
  prompt_version TEXT,
  explanation TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(work_id, topic_id, assignment_method)
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  work_version_id TEXT NOT NULL REFERENCES work_versions(id) ON DELETE CASCADE,
  document_role TEXT NOT NULL CHECK (document_role IN ('paper_pdf','paper_html','source','supplement','model_card','readme','other')),
  source_url TEXT NOT NULL,
  local_path TEXT NOT NULL,
  media_type TEXT NOT NULL,
  byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
  sha256 TEXT NOT NULL,
  parser_name TEXT,
  parser_version TEXT,
  parse_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (parse_status IN ('pending','parsed','partial','failed','quarantined')),
  page_count INTEGER,
  acquired_at TEXT NOT NULL,
  parsed_at TEXT,
  UNIQUE(work_version_id, document_role, sha256)
);

CREATE TABLE IF NOT EXISTS evidence_spans (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  section_path TEXT,
  page_start INTEGER,
  page_end INTEGER,
  char_start INTEGER,
  char_end INTEGER,
  span_text TEXT NOT NULL,
  normalized_text_sha256 TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_document_page
  ON evidence_spans(document_id, page_start);

CREATE TABLE IF NOT EXISTS ranking_profiles (
  id TEXT PRIMARY KEY,
  profile_key TEXT NOT NULL,
  version INTEGER NOT NULL,
  weights_json TEXT NOT NULL,
  normalization_json TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0,1)),
  created_at TEXT NOT NULL,
  UNIQUE(profile_key, version)
);

CREATE TABLE IF NOT EXISTS ranking_results (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  profile_id TEXT NOT NULL REFERENCES ranking_profiles(id),
  score_kind TEXT NOT NULL CHECK (score_kind IN ('technical','commercial','deep_dive_priority')),
  total_score REAL NOT NULL CHECK (total_score >= 0 AND total_score <= 100),
  components_json TEXT NOT NULL,
  feature_snapshot_json TEXT NOT NULL,
  calculated_at TEXT NOT NULL,
  UNIQUE(work_id, profile_id, score_kind)
);

CREATE INDEX IF NOT EXISTS idx_ranking_profile_score
  ON ranking_results(profile_id, score_kind, total_score DESC);

CREATE TABLE IF NOT EXISTS model_profiles (
  id TEXT PRIMARY KEY,
  profile_key TEXT NOT NULL UNIQUE,
  runtime TEXT NOT NULL,
  model_name TEXT NOT NULL,
  quantization TEXT,
  context_limit INTEGER NOT NULL,
  generation_config_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_versions (
  id TEXT PRIMARY KEY,
  prompt_key TEXT NOT NULL,
  version INTEGER NOT NULL,
  template_sha256 TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  template_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(prompt_key, version)
);

CREATE TABLE IF NOT EXISTS analysis_runs (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  work_version_id TEXT NOT NULL REFERENCES work_versions(id),
  analysis_type TEXT NOT NULL CHECK (analysis_type IN ('fast_brief','deep_dive','skeptic_review','business_analysis','code_analysis')),
  status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','rejected')),
  model_profile_id TEXT REFERENCES model_profiles(id),
  prompt_version_id TEXT REFERENCES prompt_versions(id),
  input_fingerprint TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  duration_ms INTEGER,
  error_code TEXT,
  error_detail TEXT,
  output_json TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(analysis_type, input_fingerprint, model_profile_id, prompt_version_id)
);

CREATE TABLE IF NOT EXISTS analysis_sections (
  id TEXT PRIMARY KEY,
  analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
  section_key TEXT NOT NULL,
  section_order INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft','reviewed','verified','rejected')),
  content_markdown TEXT NOT NULL,
  structured_json TEXT NOT NULL,
  confidence REAL CHECK (confidence >= 0 AND confidence <= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(analysis_run_id, section_key)
);

CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  analysis_section_id TEXT NOT NULL REFERENCES analysis_sections(id) ON DELETE CASCADE,
  claim_text TEXT NOT NULL,
  claim_type TEXT NOT NULL CHECK (claim_type IN ('fact','interpretation','recommendation','hypothesis')),
  importance TEXT NOT NULL CHECK (importance IN ('minor','major','critical')),
  verification_status TEXT NOT NULL CHECK (verification_status IN ('unsupported','supported','conflicted','rejected')),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_evidence (
  claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  evidence_span_id TEXT NOT NULL REFERENCES evidence_spans(id),
  relation TEXT NOT NULL CHECK (relation IN ('supports','contradicts','qualifies','background')),
  relevance REAL NOT NULL CHECK (relevance >= 0 AND relevance <= 1),
  PRIMARY KEY(claim_id, evidence_span_id, relation)
);

CREATE TABLE IF NOT EXISTS repository_links (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  repository_url TEXT NOT NULL,
  host TEXT NOT NULL DEFAULT 'github',
  relationship TEXT NOT NULL CHECK (relationship IN ('official','author_linked','community','unknown')),
  license_spdx TEXT,
  archived INTEGER CHECK (archived IN (0,1)),
  default_branch TEXT,
  latest_commit_at TEXT,
  latest_release_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  checked_at TEXT NOT NULL,
  UNIQUE(work_id, repository_url)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id TEXT PRIMARY KEY,
  run_type TEXT NOT NULL CHECK (run_type IN ('discover','normalize','rank','brief','deep_dive','daily','cleanup')),
  trigger_type TEXT NOT NULL CHECK (trigger_type IN ('manual','schedule','retry')),
  status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled','deferred')),
  config_snapshot_json TEXT NOT NULL,
  queued_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  error_summary TEXT
);

CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_run_id TEXT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('debug','info','warning','error','critical')),
  message TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_events_run_time
  ON job_events(pipeline_run_id, occurred_at);

CREATE TABLE IF NOT EXISTS user_feedback (
  id TEXT PRIMARY KEY,
  work_id TEXT REFERENCES works(id) ON DELETE SET NULL,
  analysis_run_id TEXT REFERENCES analysis_runs(id) ON DELETE SET NULL,
  feedback_type TEXT NOT NULL CHECK (feedback_type IN ('useful','irrelevant','too_shallow','incorrect','follow_topic','mute_topic','note')),
  value_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
  id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('work','evidence','analysis_section')),
  entity_id TEXT NOT NULL,
  model_name TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  content_sha256 TEXT NOT NULL,
  vector_store_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(entity_type, entity_id, model_name, content_sha256)
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
  entity_type UNINDEXED,
  entity_id UNINDEXED,
  title,
  body,
  topics,
  tokenize = 'unicode61 remove_diacritics 2'
);

-- Added by the versioned V1 intelligence-output migration.
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
  stage_key TEXT NOT NULL
    CHECK (stage_key IN ('extract','analyze','skeptic_check','verify_citations','publish')),
  stage_order INTEGER NOT NULL CHECK (stage_order BETWEEN 1 AND 5),
  status TEXT NOT NULL
    CHECK (status IN ('pending','running','succeeded','failed','rejected')),
  input_fingerprint TEXT NOT NULL,
  output_json TEXT,
  error_code TEXT,
  started_at TEXT,
  completed_at TEXT,
  PRIMARY KEY(job_id, stage_key)
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

CREATE TABLE IF NOT EXISTS daily_reports (
  report_date TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL CHECK (schema_version='1.0'),
  pipeline_run_id TEXT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  input_fingerprint TEXT NOT NULL,
  report_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- V1.1 multi-source artifacts and deterministic linked development events.
CREATE TABLE IF NOT EXISTS source_artifacts (
  id TEXT PRIMARY KEY,
  source_record_id TEXT NOT NULL UNIQUE REFERENCES source_records(id) ON DELETE CASCADE,
  source_key TEXT NOT NULL,
  upstream_id TEXT NOT NULL,
  artifact_type TEXT NOT NULL CHECK (artifact_type IN ('paper','repository','release','model','dataset','space','official_post')),
  source_type TEXT NOT NULL DEFAULT 'other' CHECK (source_type IN ('paper','repository','release','model','dataset','space','official_post','video','community_discussion','article','watchlist_post','x_post','other')),
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
  confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence >= 0 AND confidence <= 1),
  matching_evidence_json TEXT NOT NULL DEFAULT '[]',
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  corrected_at TEXT,
  correction_reason TEXT,
  PRIMARY KEY(event_id, artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_source_artifacts_source_updated
ON source_artifacts(source_key, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_linked_events_work ON linked_events(primary_work_id, updated_at DESC);

-- Weekend Beta agent execution and operator correction extensions (migration 0007).
CREATE TABLE IF NOT EXISTS agent_executions (
  id TEXT PRIMARY KEY,
  pipeline_run_id TEXT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL,
  agent_version TEXT NOT NULL,
  stage_order INTEGER NOT NULL CHECK(stage_order BETWEEN 1 AND 14),
  responsibility TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','skipped')),
  idempotency_key TEXT NOT NULL UNIQUE,
  attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 2),
  input_json TEXT NOT NULL DEFAULT '{}', output_json TEXT,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  provenance_refs_json TEXT NOT NULL DEFAULT '[]',
  metrics_json TEXT NOT NULL DEFAULT '{}', safe_failure_reason TEXT,
  started_at TEXT, completed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(pipeline_run_id, agent_id)
);

CREATE TABLE IF NOT EXISTS watchlist_inputs (
  id TEXT PRIMARY KEY,
  source_kind TEXT NOT NULL CHECK(source_kind IN
    ('youtube','reddit','medium','substack','rss','github','x_export')),
  label TEXT NOT NULL, location TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(source_kind, location)
);
