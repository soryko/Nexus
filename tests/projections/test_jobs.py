import pytest
from graph_case import case
from nexus_memory.domain.errors import RevisionConflict


def queue_case(tmp_path):
    _, repo, binding = case(tmp_path)
    from nexus_memory.projections.jobs import JobQueue, JobSpec, JobResult
    clock = [0]
    q = JobQueue(repo,binding,clock=lambda: clock[0],
                 validate_publication=lambda db,row: row['authority_generation'] == row['policy_generation'] == 1)
    spec = JobSpec('memory','m','r','d','index','v',1,1,1)
    ref = q.enqueue(spec)
    assert q.enqueue(spec) == ref
    return q,ref.job_id,clock,JobResult


def test_job_lease_expiry_does_not_duplicate_effect(tmp_path):
    q,j,clock,result = queue_case(tmp_path)
    a = q.claim('worker',0)
    q.renew(j,a.lease_token,1)
    assert q.claim('other',60) is None
    clock[0] = 61
    with pytest.raises(RevisionConflict):
        q.finish(j,a.lease_token,result('ready','digest'))
    b = q.claim('other',61)
    with pytest.raises(RevisionConflict):
        q.finish(j,a.lease_token,result('ready','digest'))
    assert q.finish(j,b.lease_token,result('ready','digest')).state == 'ready'
    assert q.claim('third',200) is None


def test_claim_retry_schedule(tmp_path):
    q,j,clock,result = queue_case(tmp_path)
    for attempt,delay in enumerate((1,10,60,None),1):
        lease = q.claim('w',clock[0])
        assert lease.attempt == attempt
        receipt = q.finish(j,lease.lease_token,result('transient'))
        if delay:
            assert q.claim('w',clock[0]+delay-.1) is None
            clock[0] += delay
        else:
            assert receipt.state == 'failed' and q.claim('w',10000) is None
