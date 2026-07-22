ALTER TABLE source_artifacts ADD COLUMN source_type TEXT NOT NULL DEFAULT 'other'
  CHECK(source_type IN ('paper','repository','release','model','dataset','space',
    'official_post','video','community_discussion','article','watchlist_post','x_post','other'));

UPDATE source_artifacts SET source_type=artifact_type;
