"""Resume only an existing launcher-issued grant. No model-supplied context IDs."""
import json
import os
from pathlib import Path
import stat

from nexus_memory.domain.errors import GrantRevoked, InvalidInput
from nexus_memory.domain.spaces import OperationRef
from nexus_memory.spaces.catalog import Catalog
from nexus_memory.transport.broker import Broker


def register(catalog, principal, space_id, *, stream, receiving_context, mode='read_only'):
    """Launcher-only: stream and receiving_context must be stable host-owned identities."""
    import hashlib
    key = hashlib.sha256(receiving_context.encode()).hexdigest()
    session = catalog.launch_context(principal,operation=OperationRef(stream,'launch:'+key))
    return catalog.bind_session(session,principal,space_id,alias='default',mode=mode,
                                operation=OperationRef(stream,'mount:'+key))


def resume(catalog_path,file,project_id=None,checkout=None):
    with os.fdopen(os.open(file,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)) as src:
        meta = os.fstat(src.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077 or meta.st_size>4096:
            raise InvalidInput('invalid grant file')
        ticket = json.load(src)
    if ticket.get('project_id') != project_id or not catalog_path.is_file():
        raise InvalidInput('invalid launch binding')
    catalog = Catalog(catalog_path)
    with catalog._transaction() as db:
        row = db.execute('select * from sessions where grant_id=?',(ticket['grant_id'],)).fetchone()
        if not row: raise GrantRevoked('grant unavailable')
        grant = catalog._grant(db,row)
    broker = Broker(catalog,grant)
    if checkout is not None:
        from nexus_memory.git import GitCli
        from nexus_memory.git.cli import GitCliVerifier
        from nexus_memory.git.token import read_token
        from nexus_memory.lifecycle.contracts import VerifiedCheckout
        git = GitCli()
        info = git.inspect(checkout)
        token = read_token(info.common_dir)
        binding = broker.repo.checkout_binding(broker.binding.scope,token) if token else None
        if not binding or binding.repository_id not in {r.repository_id for r in broker.binding.repository_bindings}:
            raise InvalidInput('checkout not bound to space')
        verifier = GitCliVerifier(git,checkout,binding.object_format,info.common_dir,token)
        head = verifier.resolve_commit('HEAD')
        if not head: raise InvalidInput('checkout unavailable')
        broker.checkout = VerifiedCheckout(binding,head)
        broker.memory.binding,broker.memory.verifier = binding,verifier
    return broker
