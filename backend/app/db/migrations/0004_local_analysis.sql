CREATE TABLE IF NOT EXISTS document_pages (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_number INTEGER NOT NULL CHECK (page_number >= 1),
  extraction_class TEXT NOT NULL
    CHECK (extraction_class IN ('native_text','empty','suspicious','ocr_required')),
  native_character_count INTEGER NOT NULL CHECK (native_character_count >= 0),
  image_count INTEGER NOT NULL CHECK (image_count >= 0),
  extraction_method TEXT NOT NULL CHECK (extraction_method IN ('pymupdf','tesseract','none')),
  detail TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(document_id, page_number)
);

CREATE INDEX IF NOT EXISTS idx_document_pages_class
  ON document_pages(extraction_class, document_id);

CREATE INDEX IF NOT EXISTS idx_analysis_work_type_status
  ON analysis_runs(work_id, analysis_type, status, created_at DESC);

