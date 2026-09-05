CREATE TABLE blobs (
  id INTEGER PRIMARY KEY,
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  digest BLOB NOT NULL,
  body BLOB NOT NULL,
  UNIQUE(namespace, actor, digest)
);
CREATE TABLE memories (
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  memory_id TEXT NOT NULL,
  current_revision_id TEXT NOT NULL,
  tombstoned INTEGER NOT NULL DEFAULT 0 CHECK(tombstoned IN (0,1)),
  PRIMARY KEY(namespace, actor, memory_id)
);
CREATE TABLE revisions (
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  memory_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  parent_revision_id TEXT,
  blob_id INTEGER NOT NULL REFERENCES blobs(id),
  kind TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  source_uri TEXT,
  snapshot TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(namespace, actor, memory_id, revision_id),
  FOREIGN KEY(namespace, actor, memory_id) REFERENCES memories(namespace, actor, memory_id)
);
CREATE TABLE receipts (
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  memory_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  durable_seq INTEGER NOT NULL UNIQUE,
  operation TEXT NOT NULL,
  PRIMARY KEY(namespace, actor, idempotency_key)
);
CREATE TABLE outbox (
  durable_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  operation_id TEXT NOT NULL UNIQUE,
  payload TEXT NOT NULL
);
