"""Explicit, per-launch owner capability; never inferred from a viewer binding."""
import json
import secrets
from threading import Lock
from time import time

from nexus_memory.domain.errors import FlowDenied, InvalidInput
from nexus_memory.domain.spaces import OperationRef
from nexus_memory.graph.service import GraphService
from nexus_memory.spaces import admin
from nexus_memory.spaces.catalog import Catalog
from nexus_memory.storage.graph_sqlite import GraphRepository


from .graph_queries import ScopeLost


class ApprovalInvalid(FlowDenied):
    pass


class AtlasOwner:
    def __init__(self, catalog_path, *, lifetime=900):
        self.catalog = Catalog(catalog_path)
        self.expires = time()+lifetime
        self.pending = None
        self.lock = Lock()

    def command(self, action, body, *, grant):
        with self.lock, self.catalog._transaction() as db:
            if time() >= self.expires: raise ScopeLost
            sid = body['space']
            mount = db.execute('SELECT * FROM sessions WHERE grant_id=?',(grant.grant_id,)).fetchone()
            if not mount or self.catalog._grant(db,mount) != grant or mount['space_id'] != sid:
                raise ScopeLost
            if mount['mode'] != 'read_write' or grant.exposed_space_ids - {sid}:
                raise FlowDenied('mount unavailable')
            preview = action == 'preview'
            fields = {'family','space','operation_key','request'}
            if set(body) != (fields if preview else fields | {'approval'}): raise InvalidInput('invalid command')
            family = body['family']
            if family not in ('curate','correct') or not preview and family != action: raise InvalidInput('invalid family')
            command = 'graph '+family
            args, kwargs = admin.graph_args(command,body['request'])
            path, binding = admin.space(self.catalog,db,sid)
            stream, = db.execute('SELECT stream FROM admin_stream WHERE id=1').fetchone()
            op = OperationRef(stream,body['operation_key'])
            digest = admin.approval_digest(command,op,sid,body['request'],[sid])
            if preview:
                token = secrets.token_urlsafe(32)
                self.pending = (token,digest,min(self.expires,time()+120))
                return {'approval':token,'digest':digest,'command':body}
            pending, self.pending = self.pending, None
            if not pending or pending[:2] != (body['approval'],digest) or time() >= pending[2]:
                raise ApprovalInvalid
            intent = self.catalog._operation(db,op,'owner',command,digest)
            repo = GraphRepository(path)
            git = admin.enrolled_git(db,path,binding,args[0]) if family == 'curate' else None
            service = GraphService(repo,binding,git_binding=git,prepare_only=True)
            apply = getattr(service,family)(*args,operation=op,**kwargs)
            try:
                receipt = apply(admin.ApprovedWrite(repo,binding,pending[2]))
            except FlowDenied:
                if time() >= pending[2]: raise ApprovalInvalid from None
                raise
            admin._record_graph_receipt(self.catalog,db,intent,receipt)
            return json.loads(receipt.to_json())
