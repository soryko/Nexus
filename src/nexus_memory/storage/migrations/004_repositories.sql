CREATE TABLE repositories (
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  repository_id TEXT NOT NULL,
  object_format TEXT NOT NULL,
  locator TEXT,
  registered_at TEXT NOT NULL,
  PRIMARY KEY(namespace, actor, repository_id)
);
CREATE TABLE repository_checkouts (
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  token TEXT NOT NULL,
  repository_id TEXT NOT NULL,
  registered_at TEXT NOT NULL,
  PRIMARY KEY(namespace, actor, token),
  FOREIGN KEY(namespace, actor, repository_id) REFERENCES repositories(namespace, actor, repository_id)
);
CREATE TABLE revision_references (
  namespace TEXT NOT NULL,
  actor TEXT NOT NULL,
  memory_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  repository_id TEXT NOT NULL,
  commit_oid TEXT NOT NULL,
  path TEXT NOT NULL,
  object_oid TEXT NOT NULL,
  entry_type TEXT NOT NULL,
  mode TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  PRIMARY KEY(namespace, actor, memory_id, revision_id, commit_oid, path),
  FOREIGN KEY(namespace, actor, memory_id, revision_id) REFERENCES revisions(namespace, actor, memory_id, revision_id),
  FOREIGN KEY(namespace, actor, repository_id) REFERENCES repositories(namespace, actor, repository_id)
)
