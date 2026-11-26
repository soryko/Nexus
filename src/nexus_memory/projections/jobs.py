"""Durable partition-local work leases. No dependency on capture or worker code."""
from dataclasses import astuple, dataclass
from math import isfinite
from time import time
from uuid import uuid4

from nexus_memory.domain.errors import InvalidInput, RevisionConflict


@dataclass(frozen=True)
class JobSpec:
    source_kind: str
    source_id: str
    source_revision_id: str
    source_digest: str
    job_kind: str
    producer_version: str
    index_generation: int
    authority_generation: int
    policy_generation: int

    def __post_init__(self):
        fields = astuple(self)
        if not all(type(x) is str and x for x in fields[:6]) or not all(type(x) is int and x>=0 for x in fields[6:]):
            raise InvalidInput('invalid job spec')


@dataclass(frozen=True)
class JobRef:
    job_id: str


@dataclass(frozen=True)
class LeasedJob(JobRef):
    spec: JobSpec
    lease_token: str
    expiry: float
    attempt: int


@dataclass(frozen=True)
class JobResult:
    state: str
    digest: str | None = None

    def __post_init__(self):
        if self.state not in ('ready','stale','transient','failed') or (self.state=='ready') != bool(self.digest):
            raise InvalidInput('invalid job result')


@dataclass(frozen=True)
class JobReceipt(JobRef):
    state: str
    result_digest: str | None


def instant(now):
    if type(now) not in (float,int) or not isfinite(now) or now<0:
        raise InvalidInput('invalid job time')
    return now


class JobQueue:
    def __init__(self, repository, binding, *, clock=time, validate_publication=None):
        self.repository,self.binding,self.clock = repository,binding,clock
        # A publishing worker can check its authoritative source/policy generations
        # inside this same transaction. The queue itself publishes only the digest.
        self.validate_publication = validate_publication

    def _transaction(self):
        return self.repository.transaction(self.binding,write=True)

    def enqueue(self, spec):
        attrs = astuple(spec)
        s = self.binding.space_id
        with self._transaction() as db:
            prior = db.execute('SELECT job_id FROM projection_jobs WHERE space_id=? AND source_kind=? AND source_id=? AND source_revision_id=? AND source_digest=? AND job_kind=? AND producer_version=? AND index_generation=?',(s,*attrs[:7])).fetchone()
            if prior:
                return JobRef(prior[0])
            key = str(uuid4())
            db.execute('INSERT INTO projection_jobs(space_id,job_id,source_kind,source_id,source_revision_id,source_digest,job_kind,producer_version,index_generation,authority_generation,policy_generation) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(s,key,*attrs))
            return JobRef(key)

    def claim(self, worker_id, now):
        instant(now)
        if not isinstance(worker_id,str) or not worker_id:
            raise InvalidInput('invalid worker identity')
        s = self.binding.space_id
        with self._transaction() as db:
            db.execute("UPDATE projection_jobs SET state='failed',error='lease_expired',token=NULL,expiry=NULL WHERE space_id=? AND state='running' AND expiry<=? AND attempt>=4",(s,now))
            row = db.execute("SELECT * FROM projection_jobs WHERE space_id=? AND ((state='pending' AND next_attempt<=?) OR (state='running' AND expiry<=?)) ORDER BY next_attempt,job_id LIMIT 1",(s,now,now)).fetchone()
            if not row:
                return None
            token = str(uuid4())
            db.execute("UPDATE projection_jobs SET state='running', attempt=attempt+1,worker=?,token=?,expiry=? WHERE space_id=? AND job_id=?",(worker_id,token,now+60,s,row['job_id']))
            return LeasedJob(row['job_id'],JobSpec(**{k:row[k] for k in JobSpec.__dataclass_fields__}),token,now+60,row['attempt']+1)

    def _held(self, db, job_id, lease_token, now):
        row = db.execute("SELECT * FROM projection_jobs WHERE space_id=? AND job_id=? AND state='running' AND token=? AND expiry>?",(self.binding.space_id,job_id,lease_token,instant(now))).fetchone()
        if not row:
            raise RevisionConflict('lease no longer held')
        return row

    def renew(self, job_id, lease_token, now):
        with self._transaction() as db:
            self._held(db,job_id,lease_token,now)
            db.execute('UPDATE projection_jobs SET expiry=? WHERE space_id=? AND job_id=?',(now+60,self.binding.space_id,job_id))

    def finish(self, job_id, lease_token, result):
        with self._transaction() as db:
            now = self.clock()
            row = self._held(db,job_id,lease_token,now)
            state,digest = result.state,result.digest
            error,due = None,now
            if state=='transient':
                delays = (1,10,60)
                attempt = row['attempt']
                state = 'failed' if attempt>len(delays) else 'pending'
                if state=='pending':
                    due += delays[attempt-1]
                error = 'transient'
            elif state=='ready':
                # No authority/source validator means no permission to publish.
                if self.validate_publication is None or not self.validate_publication(db,row):
                    state,digest = 'stale',None
                self._held(db,job_id,lease_token,self.clock())
            elif state=='failed':
                error = 'worker_failed'
            db.execute('UPDATE projection_jobs SET state=?,result_digest=?,error=?,next_attempt=?,token=NULL,expiry=NULL,worker=NULL WHERE space_id=? AND job_id=?',(state,digest,error,due,self.binding.space_id,job_id))
            return JobReceipt(job_id,state,digest)
