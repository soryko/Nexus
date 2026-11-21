CREATE TABLE graph_events (
 space_id TEXT NOT NULL REFERENCES space_partitions(space_id), seq INTEGER NOT NULL,
 operation_id TEXT NOT NULL, writer TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(space_id,seq));
CREATE TABLE graph_entities (
 space_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, qualified_key TEXT NOT NULL, seq INTEGER NOT NULL,
 PRIMARY KEY(space_id,id), UNIQUE(space_id,kind,qualified_key), FOREIGN KEY(space_id,seq) REFERENCES graph_events);
CREATE TABLE graph_assertions (
 space_id TEXT NOT NULL, id TEXT NOT NULL, head TEXT NOT NULL, PRIMARY KEY(space_id,id),
 FOREIGN KEY(space_id,id,head) REFERENCES graph_assertion_revisions(space_id,id,rev) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE graph_assertion_revisions (
 space_id TEXT NOT NULL, id TEXT NOT NULL, rev TEXT NOT NULL, subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
 content TEXT NOT NULL, recorded_at TEXT NOT NULL, seq INTEGER NOT NULL,
 CHECK(CASE json_extract(subject,'$.kind') WHEN 'entity' THEN
   json_type(subject,'$.entity_id') IS 'text' AND length(json_extract(subject,'$.entity_id'))>0 AND json_remove(subject,'$.kind','$.entity_id')='{}'
   WHEN 'assertion_revision' THEN json_type(subject,'$.assertion_id') IS 'text' AND json_type(subject,'$.revision_id') IS 'text' AND length(json_extract(subject,'$.assertion_id'))>0 AND length(json_extract(subject,'$.revision_id'))>0 AND json_remove(subject,'$.kind','$.assertion_id','$.revision_id')='{}' ELSE 0 END),
 CHECK(CASE json_extract(object,'$.kind') WHEN 'entity' THEN
   json_type(object,'$.entity_id') IS 'text' AND length(json_extract(object,'$.entity_id'))>0 AND json_remove(object,'$.kind','$.entity_id')='{}'
   WHEN 'assertion_revision' THEN json_type(object,'$.assertion_id') IS 'text' AND json_type(object,'$.revision_id') IS 'text' AND length(json_extract(object,'$.assertion_id'))>0 AND length(json_extract(object,'$.revision_id'))>0 AND json_remove(object,'$.kind','$.assertion_id','$.revision_id')='{}' ELSE 0 END),
 CHECK(json_extract(subject,'$.kind') IS NOT NULL AND json_extract(object,'$.kind') IS NOT NULL AND CASE
 WHEN predicate IN ('IMPLEMENTS','MOTIVATED_BY','REGRESSION_FOR','DEFINED_IN','CALLS','IMPORTS','DEPENDS_ON') THEN json_extract(subject,'$.kind')='entity' AND json_extract(object,'$.kind')='entity'
 WHEN predicate IN ('SUPPORTS','DISPUTES','SUPERSEDES') THEN json_extract(subject,'$.kind')='assertion_revision' AND json_extract(object,'$.kind')='assertion_revision'
 WHEN predicate='MENTIONS' THEN json_extract(subject,'$.kind') IN ('entity','assertion_revision') AND json_extract(object,'$.kind')='entity' ELSE 0 END),
 PRIMARY KEY(space_id,id,rev), UNIQUE(space_id,rev), FOREIGN KEY(space_id,id) REFERENCES graph_assertions, FOREIGN KEY(space_id,seq) REFERENCES graph_events);
CREATE TABLE graph_adjacency (
 space_id TEXT NOT NULL, id TEXT NOT NULL, rev TEXT NOT NULL, subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
 PRIMARY KEY(space_id,id,rev), FOREIGN KEY(space_id,id,rev) REFERENCES graph_assertion_revisions);
CREATE INDEX graph_forward ON graph_adjacency(space_id,subject,predicate,active,id,rev);
CREATE INDEX graph_reverse ON graph_adjacency(space_id,object,predicate,active,id,rev);
CREATE TABLE graph_operations (
 space_id TEXT NOT NULL, family TEXT NOT NULL CHECK(family='graph-v1'), writer TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, receipt TEXT NOT NULL, seq INTEGER NOT NULL,
 PRIMARY KEY(space_id,family,writer,key), FOREIGN KEY(space_id,seq) REFERENCES graph_events);
CREATE TABLE graph_resolution_events (
 space_id TEXT NOT NULL, generation INTEGER NOT NULL, seq INTEGER NOT NULL, change TEXT NOT NULL,
 PRIMARY KEY(space_id,generation), FOREIGN KEY(space_id,seq) REFERENCES graph_events);
CREATE TABLE graph_alias_index (
 space_id TEXT NOT NULL, member TEXT NOT NULL, target TEXT NOT NULL, action TEXT NOT NULL,
 PRIMARY KEY(space_id,member), FOREIGN KEY(space_id,target) REFERENCES graph_entities);
CREATE TABLE graph_generations (
 space_id TEXT PRIMARY KEY NOT NULL REFERENCES space_partitions(space_id), active INTEGER NOT NULL, resolution INTEGER NOT NULL);
CREATE TABLE graph_projection_state (
 space_id TEXT NOT NULL REFERENCES space_partitions(space_id), generation INTEGER NOT NULL, watermark INTEGER NOT NULL, state TEXT NOT NULL,
 PRIMARY KEY(space_id,generation));
CREATE TABLE graph_evidence (
 space_id TEXT NOT NULL REFERENCES space_partitions(space_id), id TEXT NOT NULL, source TEXT NOT NULL, span TEXT NOT NULL, captured BLOB NOT NULL, origin TEXT NOT NULL, PRIMARY KEY(space_id,id));
CREATE INDEX graph_source ON graph_evidence(space_id,source,id);
CREATE TABLE graph_assertion_evidence (
 space_id TEXT NOT NULL, id TEXT NOT NULL, rev TEXT NOT NULL, evidence TEXT NOT NULL, role TEXT NOT NULL,
 PRIMARY KEY(space_id,id,rev,evidence), FOREIGN KEY(space_id,id,rev) REFERENCES graph_assertion_revisions, FOREIGN KEY(space_id,evidence) REFERENCES graph_evidence);
CREATE INDEX graph_reverse_evidence ON graph_assertion_evidence(space_id,evidence,id,rev);
CREATE TABLE graph_evidence_state (
 space_id TEXT NOT NULL, evidence TEXT NOT NULL, head INTEGER NOT NULL, withdrawn INTEGER NOT NULL,
 PRIMARY KEY(space_id,evidence), FOREIGN KEY(space_id,evidence) REFERENCES graph_evidence, FOREIGN KEY(space_id,head) REFERENCES graph_events);
CREATE TABLE graph_support_groups (
 space_id TEXT NOT NULL, rev TEXT NOT NULL, ordinal INTEGER NOT NULL, PRIMARY KEY(space_id,rev,ordinal), FOREIGN KEY(space_id,rev) REFERENCES graph_assertion_revisions(space_id,rev));
CREATE TABLE graph_support_premises (
 space_id TEXT NOT NULL, rev TEXT NOT NULL, ordinal INTEGER NOT NULL, position INTEGER NOT NULL, evidence TEXT, dependency TEXT,
 CHECK((evidence IS NULL) != (dependency IS NULL)), PRIMARY KEY(space_id,rev,ordinal,position), FOREIGN KEY(space_id,rev,ordinal) REFERENCES graph_support_groups,
 FOREIGN KEY(space_id,evidence) REFERENCES graph_evidence, FOREIGN KEY(space_id,dependency) REFERENCES graph_assertion_revisions(space_id,rev));
CREATE TABLE graph_applicability (
 space_id TEXT NOT NULL, rev TEXT NOT NULL, attestation TEXT NOT NULL, PRIMARY KEY(space_id,rev), FOREIGN KEY(space_id,rev) REFERENCES graph_assertion_revisions(space_id,rev));
CREATE TABLE graph_retained_support (
 space_id TEXT NOT NULL, evidence TEXT NOT NULL, seq INTEGER NOT NULL, attestation TEXT NOT NULL, PRIMARY KEY(space_id,evidence,seq), FOREIGN KEY(space_id,evidence) REFERENCES graph_evidence, FOREIGN KEY(space_id,seq) REFERENCES graph_events);
CREATE TABLE graph_derived_sources (
 space_id TEXT NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(space_id,evidence), FOREIGN KEY(space_id,evidence) REFERENCES graph_evidence);
CREATE TABLE graph_source_dependencies (
 space_id TEXT NOT NULL, derived TEXT NOT NULL, required TEXT NOT NULL, PRIMARY KEY(space_id,derived,required), FOREIGN KEY(space_id,derived) REFERENCES graph_derived_sources, FOREIGN KEY(space_id,required) REFERENCES graph_evidence);
CREATE TABLE projection_jobs (
 space_id TEXT NOT NULL REFERENCES space_partitions(space_id), job_id TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_revision_id TEXT NOT NULL, source_digest TEXT NOT NULL, job_kind TEXT NOT NULL, producer_version TEXT NOT NULL, index_generation INTEGER NOT NULL, authority_generation INTEGER NOT NULL, policy_generation INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','ready','failed','stale')), attempt INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0, worker TEXT, token TEXT, expiry REAL, result_digest TEXT, error TEXT,
 PRIMARY KEY(space_id,job_id), UNIQUE(space_id,source_kind,source_id,source_revision_id,source_digest,job_kind,producer_version,index_generation));
CREATE INDEX jobs_due ON projection_jobs(space_id,state,next_attempt,expiry);
