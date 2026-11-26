CREATE TABLE store_identity (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  store_id TEXT NOT NULL UNIQUE
);
CREATE TABLE space_partitions (
  store_id TEXT NOT NULL REFERENCES store_identity(store_id),
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  space_id TEXT NOT NULL UNIQUE,
  PRIMARY KEY(namespace, actor)
);
CREATE TABLE revision_provenance (
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  memory_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  principal_id TEXT, agent_id TEXT, session_id TEXT, run_id TEXT, source_event_id TEXT,
  compat_metadata_json TEXT, publication_sources_json TEXT, visibility_role TEXT,
  PRIMARY KEY(namespace, actor, memory_id, revision_id),
  FOREIGN KEY(namespace, actor) REFERENCES space_partitions(namespace, actor),
  FOREIGN KEY(namespace, actor, memory_id, revision_id) REFERENCES revisions(namespace, actor, memory_id, revision_id)
);
CREATE TABLE operation_provenance (
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  receipt_key TEXT NOT NULL,
  principal_id TEXT, agent_id TEXT, session_id TEXT, run_id TEXT, source_event_id TEXT,
  PRIMARY KEY(namespace, actor, receipt_key),
  FOREIGN KEY(namespace, actor) REFERENCES space_partitions(namespace, actor),
  FOREIGN KEY(namespace, actor, receipt_key) REFERENCES receipts(namespace, actor, idempotency_key)
);
CREATE TABLE operation_keys (
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  family TEXT NOT NULL CHECK (family = 'exact'),
  writer_stream_id TEXT NOT NULL,
  operation_key TEXT NOT NULL,
  digest_version INTEGER NOT NULL CHECK (digest_version > 0),
  command_digest TEXT NOT NULL,
  receipt_key TEXT NOT NULL,
  PRIMARY KEY(namespace, actor, family, writer_stream_id, operation_key),
  UNIQUE(namespace, actor, receipt_key),
  FOREIGN KEY(namespace, actor) REFERENCES space_partitions(namespace, actor),
  FOREIGN KEY(namespace, actor, receipt_key) REFERENCES receipts(namespace, actor, idempotency_key)
);
