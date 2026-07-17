PRAGMA user_version = 0;

CREATE TABLE previous_release_marker (
  marker TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);

INSERT INTO previous_release_marker(marker, created_at)
VALUES ('v0-fixture', '2026-07-17T00:00:00Z');
