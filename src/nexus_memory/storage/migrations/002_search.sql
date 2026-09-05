CREATE TABLE head_index (
  seq INTEGER PRIMARY KEY,
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  memory_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  blob_id INTEGER NOT NULL REFERENCES blobs(id),
  kind TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  durable_seq INTEGER NOT NULL,
  has_parent INTEGER NOT NULL DEFAULT 0 CHECK(has_parent IN (0,1)),
  UNIQUE(namespace, actor, memory_id)
);
CREATE INDEX head_index_recent ON head_index(namespace, actor, durable_seq DESC);
CREATE TABLE tags (
  id INTEGER PRIMARY KEY,
  tag TEXT NOT NULL UNIQUE
);
CREATE TABLE head_tags (
  seq INTEGER NOT NULL REFERENCES head_index(seq) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id),
  PRIMARY KEY(seq, tag_id)
);
CREATE INDEX head_tags_tag ON head_tags(tag_id, seq);
CREATE VIEW head_body AS
  SELECT h.seq AS rowid, CAST(b.body AS TEXT) AS body
  FROM head_index h JOIN blobs b ON b.id = h.blob_id;
CREATE VIRTUAL TABLE head_fts USING fts5(body, content='head_body', content_rowid='rowid');
CREATE TABLE index_state (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  generation INTEGER NOT NULL
);
INSERT INTO index_state(id, generation) VALUES (1, 1);
ALTER TABLE receipts ADD COLUMN digest_version INTEGER NOT NULL DEFAULT 1
