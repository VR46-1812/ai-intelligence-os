CREATE VIEW IF NOT EXISTS catalog_work_search_documents AS
SELECT
  w.id AS work_id,
  w.canonical_title AS title,
  trim(
    coalesce(w.abstract, '') || ' ' ||
    coalesce((
      SELECT group_concat(a.display_name, ' ')
      FROM work_authors AS wa
      JOIN authors AS a ON a.id = wa.author_id
      WHERE wa.work_id = w.id
      ORDER BY wa.author_order
    ), '')
  ) AS body,
  coalesce((
    SELECT group_concat(t.display_name, ' ')
    FROM work_topics AS wt
    JOIN topics AS t ON t.id = wt.topic_id
    WHERE wt.work_id = w.id
  ), '') AS topics
FROM works AS w
WHERE w.work_type = 'paper';

CREATE TRIGGER IF NOT EXISTS catalog_fts_work_insert
AFTER INSERT ON works WHEN new.work_type = 'paper'
BEGIN
  INSERT INTO knowledge_fts(entity_type, entity_id, title, body, topics)
  SELECT 'work', work_id, title, body, topics
  FROM catalog_work_search_documents WHERE work_id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS catalog_fts_work_update
AFTER UPDATE OF canonical_title, abstract, work_type ON works
BEGIN
  DELETE FROM knowledge_fts WHERE entity_type = 'work' AND entity_id = old.id;
  INSERT INTO knowledge_fts(entity_type, entity_id, title, body, topics)
  SELECT 'work', work_id, title, body, topics
  FROM catalog_work_search_documents WHERE work_id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS catalog_fts_work_delete
AFTER DELETE ON works
BEGIN
  DELETE FROM knowledge_fts WHERE entity_type = 'work' AND entity_id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS catalog_fts_author_insert
AFTER INSERT ON work_authors
BEGIN
  DELETE FROM knowledge_fts WHERE entity_type = 'work' AND entity_id = new.work_id;
  INSERT INTO knowledge_fts(entity_type, entity_id, title, body, topics)
  SELECT 'work', work_id, title, body, topics
  FROM catalog_work_search_documents WHERE work_id = new.work_id;
END;

CREATE TRIGGER IF NOT EXISTS catalog_fts_author_delete
AFTER DELETE ON work_authors
BEGIN
  DELETE FROM knowledge_fts WHERE entity_type = 'work' AND entity_id = old.work_id;
  INSERT INTO knowledge_fts(entity_type, entity_id, title, body, topics)
  SELECT 'work', work_id, title, body, topics
  FROM catalog_work_search_documents WHERE work_id = old.work_id;
END;

CREATE TRIGGER IF NOT EXISTS catalog_fts_author_name_update
AFTER UPDATE OF display_name ON authors
BEGIN
  DELETE FROM knowledge_fts
  WHERE entity_type = 'work'
    AND entity_id IN (SELECT work_id FROM work_authors WHERE author_id = new.id);
  INSERT INTO knowledge_fts(entity_type, entity_id, title, body, topics)
  SELECT 'work', work_id, title, body, topics
  FROM catalog_work_search_documents
  WHERE work_id IN (SELECT work_id FROM work_authors WHERE author_id = new.id);
END;

CREATE TRIGGER IF NOT EXISTS catalog_fts_topic_insert
AFTER INSERT ON work_topics
BEGIN
  DELETE FROM knowledge_fts WHERE entity_type = 'work' AND entity_id = new.work_id;
  INSERT INTO knowledge_fts(entity_type, entity_id, title, body, topics)
  SELECT 'work', work_id, title, body, topics
  FROM catalog_work_search_documents WHERE work_id = new.work_id;
END;

CREATE TRIGGER IF NOT EXISTS catalog_fts_topic_delete
AFTER DELETE ON work_topics
BEGIN
  DELETE FROM knowledge_fts WHERE entity_type = 'work' AND entity_id = old.work_id;
  INSERT INTO knowledge_fts(entity_type, entity_id, title, body, topics)
  SELECT 'work', work_id, title, body, topics
  FROM catalog_work_search_documents WHERE work_id = old.work_id;
END;

CREATE TRIGGER IF NOT EXISTS catalog_fts_topic_name_update
AFTER UPDATE OF display_name ON topics
BEGIN
  DELETE FROM knowledge_fts
  WHERE entity_type = 'work'
    AND entity_id IN (SELECT work_id FROM work_topics WHERE topic_id = new.id);
  INSERT INTO knowledge_fts(entity_type, entity_id, title, body, topics)
  SELECT 'work', work_id, title, body, topics
  FROM catalog_work_search_documents
  WHERE work_id IN (SELECT work_id FROM work_topics WHERE topic_id = new.id);
END;

DELETE FROM knowledge_fts WHERE entity_type = 'work';
INSERT INTO knowledge_fts(entity_type, entity_id, title, body, topics)
SELECT 'work', work_id, title, body, topics FROM catalog_work_search_documents;
