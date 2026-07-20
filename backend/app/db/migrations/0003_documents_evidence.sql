CREATE TABLE IF NOT EXISTS document_acquisition_attempts (
  id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  work_version_id TEXT NOT NULL REFERENCES work_versions(id) ON DELETE CASCADE,
  source_url TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('succeeded','failed','quarantined')),
  error_code TEXT,
  safe_detail TEXT,
  quarantine_path TEXT,
  attempted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_attempt_work_time
  ON document_acquisition_attempts(work_id, attempted_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_span_identity
  ON evidence_spans(document_id, normalized_text_sha256, page_start, char_start);

CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
  evidence_id UNINDEXED,
  document_id UNINDEXED,
  span_text,
  section_path,
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS evidence_spans_ai AFTER INSERT ON evidence_spans BEGIN
  INSERT INTO evidence_fts(evidence_id, document_id, span_text, section_path)
  VALUES (new.id, new.document_id, new.span_text, COALESCE(new.section_path, ''));
END;

CREATE TRIGGER IF NOT EXISTS evidence_spans_ad AFTER DELETE ON evidence_spans BEGIN
  DELETE FROM evidence_fts WHERE evidence_id = old.id;
END;

INSERT INTO evidence_fts(evidence_id, document_id, span_text, section_path)
SELECT e.id, e.document_id, e.span_text, COALESCE(e.section_path, '')
FROM evidence_spans e
WHERE NOT EXISTS (SELECT 1 FROM evidence_fts f WHERE f.evidence_id = e.id);
