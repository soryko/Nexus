"""Local owner commands. No implicit profile, destination, checkout or credential."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import stat
from dataclasses import replace
from tempfile import TemporaryDirectory
from time import time as _now
from uuid import uuid4

from nexus_memory.domain.errors import (FlowDenied, InvalidInput, NexusError, SpaceNotMounted,
                                        StorageIntegrityError, UnsupportedCapability)
from nexus_memory.domain.graph import AssertionDraft, CurationBatch, EntityResolutionInput
from nexus_memory.domain.spaces import Mount, OperationRef, _load, _object, canonical_command_digest
from nexus_memory.git.cli import GitCli, GitCliVerifier
from nexus_memory.git.identity import bind_repository, read_token
from nexus_memory.graph.service import GraphService
from nexus_memory.storage import SQLiteRepository
from nexus_memory.storage.graph_sqlite import GraphRepository
from .catalog import Catalog, _bindings, _check, _reader
from .migration import backup_store, store_manifest
from project_scope import resolve as resolve_project

COMMANDS = {'catalog':['init'], 'space':['create','list','register','ceiling'], 'store':['migrate','restore'],
            'grant':['issue','revoke'], 'graph':['curate','correct','resolve'],
            'proposal':['list','review'], 'projection':['rebuild','status'], 'project':['open','ceiling','status']}
READS = {'space list','proposal list','projection status','project status'}
LIMIT = 1 << 20


def require(condition):
    if not condition:
        raise InvalidInput('invalid owner request')


def read_object(path, *, private=False):
    flags = os.O_RDONLY | os.O_NONBLOCK | (os.O_NOFOLLOW if private else 0)
    with os.fdopen(os.open(path, flags), 'rb') as file:
        info = os.fstat(file.fileno())
        require(stat.S_ISREG(info.st_mode) and (not private or
                (info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600)))
        raw = file.read(LIMIT+1)
    require(len(raw) <= LIMIT)
    obj = _load(raw.decode('utf8'))
    require(isinstance(obj, dict))
    return obj


def output_private(path, value):
    # Install a complete credential without replacing another file. On retry,
    # accept only the same private credential, not any existing writable file.
    with TemporaryDirectory(dir=path.parent) as tmp:
        staged = Path(tmp) / 'credential'
        with os.fdopen(os.open(staged, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600),'w') as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(staged, path)
        except FileExistsError:
            require(read_object(path, private=True) == value)


def active_store(catalog, db, store_id):
    row = catalog._store(db, store_id)
    _check(row['state'] == 'active')
    return Path(row['locator']), catalog._inspect(db,row)


def space(catalog, db, sid):
    row = db.execute('SELECT store_id FROM spaces WHERE space_id=?',(sid,)).fetchone()
    if not row:
        raise SpaceNotMounted('unknown space')
    path, bindings = active_store(catalog, db, row[0])
    return path, next(b for b in bindings if b.space_id == sid)


def profiles(path):
    with _reader(path) as db:
        return {arg:db.execute(f'SELECT profile FROM {table} WHERE id=1').fetchone()[0]
                for arg,table in [('index_profile','search_profile'),('history_profile','history_profile')]
                if db.execute('SELECT 1 FROM sqlite_schema WHERE name=?',(table,)).fetchone()}


class ProjectCatalog(Catalog):
    """Enroll while the created partition is still reserved, before activation."""
    def _install_bindings(self, db, bindings, title, kind, owner):
        binding, = bindings
        path = Path(self._store(db,binding.store_id)['locator'])
        store = SQLiteRepository(path,owner_schema5=True,**profiles(path))
        project = resolve_project(self.checkout, allow_catalog=True)
        repo, verifier = bind_repository(store,binding.scope,project.checkout,git=GitCli())
        require(repo is not None and repo.locator == str(project.common))
        db.execute('INSERT INTO admin_projects VALUES(?,?,?)',
                   (binding.space_id, repo.repository_id, project.id))
        require(verifier is not None)
        super()._install_bindings(db,bindings,title,kind,owner)


def enrolled_git(catalog_db, path, binding, batch):
    from nexus_memory.domain.graph import CodeSource
    ids = {e.source.repository_id for e in batch.evidence if isinstance(e.source,CodeSource)}
    if not ids:
        return None
    require(len(ids) == 1)
    with _reader(path) as db:
        row = db.execute('SELECT locator,object_format FROM repositories WHERE namespace=? AND actor=? AND repository_id=?',
                         (binding.scope.namespace,binding.scope.actor,next(iter(ids)))).fetchone()
        require(row is not None)
        project = resolve_project(Path(row[0]).parent, allow_catalog=True)
        require(project.common == Path(row[0]) and catalog_db.execute(
            'SELECT 1 FROM admin_projects WHERE space_id=? AND repository_id=? AND project_id=?',
            (binding.space_id,next(iter(ids)),project.id)).fetchone())
        checkout = project.checkout
        git = GitCli()
        info = git.inspect(checkout)
        token = read_token(info.common_dir)
        require(info.common_dir == project.common and info.object_format == row[1] and token and db.execute(
            'SELECT 1 FROM repository_checkouts WHERE namespace=? AND actor=? AND repository_id=? AND token=?',
            (binding.scope.namespace,binding.scope.actor,next(iter(ids)),token)).fetchone())
    repo = next(r for r in binding.repository_bindings if r.repository_id in ids)
    return repo, GitCliVerifier(git,checkout,row[1],info.common_dir,token)


def graph_args(family, request):
    def owner(draft):
        return replace(draft,origin_kind='human_asserted',review_state='accepted')
    if family == 'graph curate':
        batch = CurationBatch.from_json(request)
        return (replace(batch,assertions=tuple(map(owner,batch.assertions))),), {}
    if family == 'graph correct':
        r = _object(request,'assertion_id expected_revision_id replacement','expected_resolution_generation')
        require(all(isinstance(r[k],str) and r[k] for k in ('assertion_id','expected_revision_id')))
        return (r['assertion_id'],r['expected_revision_id'],owner(AssertionDraft.from_json(r['replacement']))), {
            k:r[k] for k in ('expected_resolution_generation',) if k in r}
    require(family == 'graph resolve')
    return (EntityResolutionInput.from_json(request),), {}


def approval_digest(family, op, sid, request, exposed):
    return canonical_command_digest(1,family,{'operation':json.loads(op.to_json()), 'destination':sid,
                                             'request':request,'exposed_space_ids':exposed})


class ApprovedWrite:
    def __init__(self, repo, binding, expiry):
        self.repo, self.binding, self.expiry = repo, binding, expiry

    def mutate(self, apply, lookup):
        with self.repo.transaction(self.binding,write=True) as db:
            receipt = lookup(db)
            if receipt is None:
                if _now() >= self.expiry:
                    raise FlowDenied('approval expired')
                receipt = apply(db)
                if _now() >= self.expiry:
                    raise FlowDenied('approval expired')
            return receipt


def _record_graph_receipt(catalog, db, op, receipt):
    catalog._finish(db,op,receipt.to_json())


def issue_mount(catalog, stream, handle, request, out, ticket_fields=None):
    """Publish a private ticket for a stable owner-identified receiving context."""
    r = _object(request,'principal_id receiving_context space_id mode','role')
    require(all(isinstance(v,str) and v for v in r.values()))
    key = canonical_command_digest(1,'context',{'context':r['receiving_context']})
    writer = stream+':launcher'
    session = catalog.launch_context(r['principal_id'],operation=OperationRef(writer,'launch:'+key),
                                     role=r.get('role','root'))
    operation = OperationRef(writer,'mount:'+key)
    mount = Mount('default',r['space_id'],r['mode'],r['mode']=='read_write')
    with catalog._transaction() as db:
        space(catalog,db,r['space_id'])
        require(db.execute('SELECT 1 FROM spaces WHERE space_id=? AND owner=?',
                           (r['space_id'],r['principal_id'])).fetchone())
        ticket = catalog._operation(db,operation,'bind',session,r['principal_id'],mount.to_json())['allocation']
    # Never activate a mount if its ticket cannot be delivered privately.
    output_private(out,{'grant_id':ticket,**(ticket_fields or {})})
    grant = catalog.bind_session(session,r['principal_id'],r['space_id'],alias=mount.alias,mode=mount.mode,operation=operation)
    with catalog._transaction() as db:
        db.execute('INSERT OR IGNORE INTO admin_mounts VALUES(?,?,?)',(handle,grant.grant_id,grant.epoch))
    return {'grant_id':handle,'epoch':grant.epoch}


def project_space(db, project_id):
    """The space `project open` created for a registry project: it is titled with the project ID."""
    return db.execute("SELECT spaces.space_id,ceiling,ceiling_generation FROM admin_projects "
                      "JOIN spaces USING(space_id) JOIN stores USING(store_id) "
                      "WHERE project_id=? AND title=? AND kind='project' AND state='active'",
                      (project_id,project_id)).fetchone()


def open_project(catalog, stream, allocation, request, out):
    """Host launch for a catalog repo: create its project space once, then mint a grant for this
    session alone. The grant never exceeds the space's ceiling, and a delegated session reads only."""
    r = _object(request,'project_id checkout host','role')
    require(all(isinstance(v,str) and v for v in r.values()) and r.get('role','root') in ('root','delegated'))
    project = resolve_project(Path(r['checkout']), allow_catalog=True)
    require(project.id == r['project_id'] and project.catalog is not None and project.catalog.resolve() == catalog.path)
    creator = ProjectCatalog(catalog.path)
    creator.checkout = project.common.parent
    # Every host derives the same operation, so concurrent first opens create one space.
    binding = creator.create_space(project.id,'project',principal_id='local',
                                   operation=OperationRef(stream,'project-space:'+project.id))
    with catalog._transaction() as db:   # the catalog lock serializes the fresh store's upgrade too
        path, _ = space(catalog,db,binding.space_id)
        with _reader(path) as store:
            version, = store.execute('PRAGMA user_version').fetchone()
            empty = not store.execute('SELECT 1 FROM memories LIMIT 1').fetchone()
        if version < 6:
            require(empty)               # only a store this command just created; never a migration
            SQLiteRepository(path,owner_schema6=True,**profiles(path))
        row = project_space(db,project.id)
        require(row is not None and row['space_id'] == binding.space_id)
    role = r.get('role','root')     # an 'off' space is refused by bind_session's own ceiling check
    mode = 'read_write' if row['ceiling'] == 'maintain' and role == 'root' else 'read_only'
    issue_mount(catalog,stream,allocation,{'principal_id':'local','receiving_context':r['host']+':'+allocation,
                                           'space_id':binding.space_id,'mode':mode,'role':role},
                out,{'project_id':project.id})
    return {'space_id':binding.space_id,'ceiling':row['ceiling'],'generation':row['ceiling_generation'],'mode':mode}


def backup_identity(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as f:
        st = os.fstat(f.fileno())
        _check(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == os.getuid()
               and stat.S_IMODE(st.st_mode) == 0o600)
        return [st.st_dev, st.st_ino, hashlib.file_digest(f, 'sha256').hexdigest()]


def migrate(catalog, intent, inner, store_id):
    backup = catalog.path.parent / ('backup-'+intent['allocation']+'.sqlite')
    # A verified snapshot gets its own durable operation receipt *before* the
    # store upgrade. An unclaimed file (even an identical copy) is not a receipt.
    with catalog._transaction() as db:
        path, _ = active_store(catalog, db, store_id)
        claim = catalog._operation(db, inner, 'migration-backup', store_id, str(backup))
        _check(not any(os.path.lexists(str(backup)+s) for s in ('-wal','-shm','-journal')))
        if not claim['receipt']:
            _check(not os.path.lexists(backup))
            with _reader(path) as src:
                version, = src.execute('PRAGMA user_version').fetchone()
            if version == 6:
                result = {'store_id':store_id, 'schema_version':version, 'backup':None}
                catalog._finish(db, intent, json.dumps(result))
                return result
            manifest = backup_store(path, backup)
            _check(manifest.store_id == store_id and manifest.schema_version == version)
            catalog._finish(db, claim, json.dumps(backup_identity(backup)))
    with catalog._transaction() as db:
        path, _ = active_store(catalog, db, store_id)
        claim = catalog._operation(db, inner, 'migration-backup', store_id, str(backup))
        _check(not any(os.path.lexists(str(backup)+s) for s in ('-wal','-shm','-journal')))
        _check(os.path.lexists(backup) and not backup.is_symlink()
               and backup_identity(backup) == json.loads(claim['receipt']))
        with _reader(path) as src:
            version, = src.execute('PRAGMA user_version').fetchone()
            if version < 6:
                with _reader(backup) as copy:
                    _check(store_manifest(copy) == store_manifest(src))
        if version < 6:
            SQLiteRepository(path,owner_schema6=True,**profiles(path))
        with _reader(path) as src:
            version, = src.execute('PRAGMA user_version').fetchone()
        _check(version == 6)
        result = {'store_id':store_id, 'schema_version':version, 'backup':str(backup)}
        catalog._finish(db, intent, json.dumps(result))
        return result


def initialize(catalog):
    with catalog._transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS admin_stream (id INTEGER PRIMARY KEY CHECK(id=1), stream TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO admin_stream VALUES(1,?)',(str(uuid4()),))
        db.execute('CREATE TABLE IF NOT EXISTS admin_projects (space_id TEXT, repository_id TEXT, project_id TEXT NOT NULL, PRIMARY KEY(space_id,repository_id))')
        db.execute('CREATE TABLE IF NOT EXISTS admin_mounts (handle TEXT PRIMARY KEY, ticket TEXT NOT NULL, epoch INTEGER NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS admin_approvals (id TEXT PRIMARY KEY, secret TEXT NOT NULL, '
                   'digest TEXT NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)')


def execute(args):
    cmd = args.group+' '+args.action
    require(args.action in COMMANDS[args.group] and (cmd in READS or args.operation_key))
    if args.group in ('proposal','projection'):
        raise UnsupportedCapability('later stage')
    require(bool(args.out) == (cmd in ('grant issue','project open')) and bool(args.grant_file) == (args.group == 'graph')
            and bool(args.space_id) == (args.group == 'graph'))
    request = read_object(args.request) if args.request else {}
    require(cmd in ('catalog init','project open') or args.catalog.is_file())
    if os.path.lexists(args.catalog):
        info = args.catalog.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600)
    catalog = Catalog(args.catalog)
    if cmd == 'catalog init' or args.group == 'project':
        initialize(catalog)
    with catalog._transaction() as db:
        stream = db.execute('SELECT stream FROM admin_stream WHERE id=1').fetchone()[0]
        if cmd == 'space list':
            _object(request,'')
            return {'spaces':[dict(row) for row in db.execute('SELECT spaces.* FROM spaces JOIN stores USING(store_id) '
                        "WHERE state='active' ORDER BY space_id")]}
        if cmd == 'project status':
            row = project_space(db,_object(request,'project_id')['project_id'])
            return dict(zip(('space_id','ceiling','generation'),row)) if row else {'space_id':None}
        operation = OperationRef(stream,args.operation_key)
        if args.group == 'graph':
            file = _object(read_object(args.grant_file,private=True),'grant_id secret')
            row = db.execute('SELECT * FROM admin_approvals WHERE id=?',(file['grant_id'],)).fetchone()
            digest = approval_digest(cmd,operation,args.space_id,request,[args.space_id])
            if not row or row['revoked'] or not hmac.compare_digest(row['secret'],file['secret']) or row['digest'] != digest:
                raise FlowDenied('approval mismatch')
            intent = catalog._operation(db,operation,'owner',cmd,digest)
            path,binding = space(catalog,db,args.space_id)
            repo = GraphRepository(path)
            positional,kw = graph_args(cmd,request)
            with repo.transaction(binding,write=False) as store:
                committed = store.execute("SELECT receipt FROM graph_operations WHERE family='graph-v1' AND space_id=? AND writer=? AND key=?",
                                          (binding.space_id,stream,operation.operation_key)).fetchone()
            if intent['receipt']:
                _check(committed is not None and committed[0] == intent['receipt'])
            git = enrolled_git(db,path,binding,positional[0]) if cmd == 'graph curate' and not committed else None
            svc = GraphService(repo,binding,git_binding=git,prepare_only=True)
            apply = getattr(svc,args.action)(*positional,operation=operation,**kw)
            receipt = apply(ApprovedWrite(repo,binding,row['expires']))
            _record_graph_receipt(catalog,db,intent,receipt)
            return json.loads(receipt.to_json())
        # Reserve a private substream before crossing file/transaction boundaries.
        # The outer digest covers the full CLI command, including output location.
        digest = canonical_command_digest(1,cmd,{'request':request,'out':str(args.out.absolute()) if args.out else None})
        intent = catalog._operation(db,operation,'owner',cmd,digest)
        allocation = intent['allocation']
        if intent['receipt'] and cmd != 'grant issue':
            return json.loads(intent['receipt'])
    inner = OperationRef(allocation,'apply')
    if cmd == 'catalog init':
        _object(request,'')
        result = {'initialized':True}
    elif cmd == 'space create':
        r = _object(request,'name kind','principal_id repo')
        require(all(isinstance(v,str) and v for v in r.values()))
        if 'repo' in r:
            require(r['kind'] == 'project' and Path(r['repo']).is_absolute())
            catalog = ProjectCatalog(args.catalog)
            catalog.checkout = Path(r['repo'])
        binding = catalog.create_space(r['name'],r['kind'],principal_id=r.get('principal_id','local'),operation=inner)
        with catalog._transaction() as db:
            _, binding = space(catalog,db,binding.space_id)
        result = json.loads(binding.to_json())
    elif cmd == 'space ceiling':
        r = _object(request,'space_id ceiling','principal_id')
        require(all(isinstance(v,str) and v for v in r.values()))
        generation = catalog.set_ceiling(r['space_id'],r['ceiling'],principal_id=r.get('principal_id','local'),operation=inner)
        result = {'space_id':r['space_id'],'ceiling':r['ceiling'],'generation':generation}
    elif cmd == 'project open':
        result = open_project(catalog,stream,allocation,request,args.out)
    elif cmd == 'project ceiling':
        r = _object(request,'project_id ceiling')
        require(all(isinstance(v,str) and v for v in r.values()))
        with catalog._transaction() as db:
            row = project_space(db,r['project_id'])
        result = {'space_id':None}
        if row:
            generation = catalog.set_ceiling(row['space_id'],r['ceiling'],principal_id='local',operation=inner)
            result = {'space_id':row['space_id'],'ceiling':r['ceiling'],'generation':generation}
    elif cmd == 'space register':
        r = _object(request,'path','expected_store_id')
        bindings = catalog.register_store(r['path'],expected_store_id=r.get('expected_store_id'),operation=inner)
        result = {'spaces':[json.loads(b.to_json()) for b in bindings]}
    elif cmd == 'store migrate':
        r = _object(request,'store_id')
        return migrate(catalog,intent,inner,r['store_id'])
    elif cmd == 'store restore':
        r = _object(request,'store_id path')
        path = Path(r['path']).resolve(strict=True)
        # Check the candidate before withdrawing any live grants.
        bindings = _bindings(path,r['store_id'])
        with catalog._transaction() as db:
            _check(catalog._mapped(db,r['store_id']) == {(b.space_id,b.scope.namespace,b.scope.actor) for b in bindings})
            with _reader(path) as reader:
                _check(reader.execute('PRAGMA integrity_check').fetchall() == [('ok',)] and not reader.execute('PRAGMA foreign_key_check').fetchall())
        catalog.begin_restore(r['store_id'],operation=OperationRef(allocation,'begin'))
        result = {'spaces':[json.loads(b.to_json()) for b in catalog.restore_store(r['store_id'],path,operation=inner)]}
    elif cmd == 'grant issue' and 'receiving_context' in request:
        result = issue_mount(catalog,stream,allocation,request,args.out)
    elif cmd == 'grant issue':
        r = _object(request,'family operation_key space_id request exposed_space_ids')
        target = OperationRef(stream,r['operation_key'])
        require(r['exposed_space_ids'] == [r['space_id']])  # Stage A: isolated single audience
        graph_args(r['family'],r['request'])
        digest = approval_digest(r['family'],target,r['space_id'],r['request'],r['exposed_space_ids'])
        # Persist the private allocation first. A crash after linking the file
        # but before activating it can retry with identical bytes.
        with catalog._transaction() as db:
            secret = catalog._operation(db,inner,'approval-secret')['allocation']
        with catalog._transaction() as db:
            space(catalog,db,r['space_id'])
            catalog._operation(db,target,'owner',r['family'],digest)
            db.execute('INSERT OR IGNORE INTO admin_approvals(id,secret,digest,expires) VALUES(?,?,?,?)',
                       (allocation,secret,digest,_now()+120))
            row = db.execute('SELECT * FROM admin_approvals WHERE id=?',(allocation,)).fetchone()
            require(not row['revoked'])
            output_private(args.out,{'grant_id':allocation,'secret':row['secret']})
            result = {'grant_id':allocation,'expires':row['expires']}
    elif cmd == 'grant revoke':
        r = _object(request,'grant_id')
        with catalog._transaction() as db:
            mount = db.execute('SELECT ticket,epoch FROM admin_mounts WHERE handle=?',(r['grant_id'],)).fetchone()
            if not mount:
                require(db.execute('UPDATE admin_approvals SET revoked=1 WHERE id=?',(r['grant_id'],)).rowcount == 1)
                result = {'revoked':r['grant_id']}
                catalog._finish(db,intent,json.dumps(result))
                return result
        if mount:
            from .authority import AuthorityFence
            from .queue import PublicationQueue
            AuthorityFence(catalog,PublicationQueue(catalog)).revoke(mount['ticket'],expected_epoch=mount['epoch'],operation=inner)
        result = {'revoked':r['grant_id']}
    else:
        raise UnsupportedCapability('later stage')
    with catalog._transaction() as db:
        catalog._finish(db,intent,json.dumps(result))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(prog='nexus-memory-admin',description='; '.join(k+' '+ '/'.join(v) for k,v in COMMANDS.items()))
    p.add_argument('group',choices=COMMANDS)
    p.add_argument('action')
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--operation-key')
    p.add_argument('--space-id')
    for arg in ('request','out','grant-file'):
        p.add_argument('--'+arg,type=Path)
    try:
        result = execute(p.parse_args(argv))
    except (NexusError,OSError,ValueError,TypeError,sqlite3.Error,RecursionError) as exc:
        print(json.dumps({'error':exc.code if isinstance(exc,NexusError) else 'invalid_input'}))
        return 1
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    return 0
