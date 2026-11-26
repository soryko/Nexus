import pytest
from fixtures.v2.lease_case import context, lease_case
from nexus_memory.domain.retrieval import RecallQuery


@pytest.fixture
def lease(tmp_path):
    return lease_case(tmp_path)


@pytest.fixture
def reader(lease):
    from nexus_memory.retrieval.service import RecallService
    return RecallService(lease.repo, lease.binding)


@pytest.fixture
def ctx(lease):
    def fresh():
        with lease.repo.open_snapshot(lease.binding) as snap:
            return context(lease, snap, lease.R)
    return fresh


@pytest.fixture
def compound():
    return RecallQuery(anchors=('renew_lease',), intent='compound')
