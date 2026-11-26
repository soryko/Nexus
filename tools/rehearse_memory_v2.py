"""Rollback drill: only private temporary databases; requires local baseline Git object."""
import io
import json
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from check_memory_v2 import Fixture, ROOT, require
from nexus_memory.spaces.migration import backup_store

BASE = '4ce3a5b'
# Run each operation in a new interpreter. For baseline runs the archived source
# takes precedence over the current installation; no old wheel download needed.
WORKER = '''
import json,sys
from dataclasses import asdict
from pathlib import Path
old,path,checkout,action,*args=sys.argv[1:]
if old: sys.path.insert(0,old)
from nexus_memory.storage import SQLiteRepository
from nexus_memory.memory import MemoryService
from nexus_memory.domain.models import Scope,MemoryInput,SearchQuery,ReferenceInput
from nexus_memory.git import GitCli,bind_repository
if old: assert Path(sys.modules[SQLiteRepository.__module__].__file__).is_relative_to(old)
store=SQLiteRepository(path,**({} if old else {'owner_schema6':True}))
scope=Scope('fixture','local')
binding,verifier=bind_repository(store,scope,Path(checkout),None,GitCli())
m=MemoryService(store,scope,binding,verifier)
item=MemoryInput('synthetic grace café\\r\\n',references=(ReferenceInput('test.txt','HEAD'),))
if action=='record': result=m.record(item,'one')
elif action=='get': result=m.get(*args)
elif action=='search': result=m.search(SearchQuery(query='grace'))
elif action=='revise': result=m.revise(*args,MemoryInput('corrected'), 'edit')
elif action=='forget': result=m.forget(*args,'forget')
print(json.dumps(asdict(result)))
'''


def rehearse(root):
    f = Fixture(root)
    sha = f.run('git','-C',ROOT,'rev-parse',BASE+'^{commit}')
    archive = subprocess.check_output(['git','-C',ROOT,'archive',sha,'src/nexus_memory'],env=f.env)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root/'old',filter='data')
    old = root/'old/src'
    f.vcs('init','-q'); (f.git/'test.txt').write_text('fixture'); f.vcs('add','.'); f.vcs('commit','-qm','seed')
    path = root/'seed'
    def worker(action,*args,baseline=False):
        return json.loads(f.run(sys.executable,'-I','-c',WORKER,old if baseline else '',path,f.git,action,*args))
    r=worker('record',baseline=True); mid=r['memory_id']; rid=r['revision_id']
    before=worker('get',mid,baseline=True)
    # Last writer has exited; use the SQLite backup API, not a raw WAL file copy.
    original=backup_store(path,root/'v4-backup'); path=root/'upgrade'
    require(original.schema_version==4 and backup_store(root/'v4-backup',path)==original,'verified v4 copy')
    f.admin('catalog init','init'); space,=f.admin('space register','register',{'path':str(path)})['spaces']
    def schema():
        with sqlite3.connect(path) as db: return db.execute('pragma user_version').fetchone()[0]
    require(schema()==5,'005'); f.admin('store migrate','migrate',{'store_id':space['store_id']}); require(schema()==6,'006')
    # Actual old server startup, not a patched SCHEMA_VERSION. Require the specific refusal.
    cmd=[sys.executable,'-I','-c',"import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('nexus_memory',run_name='__main__')",
         str(old),'--db',str(path),'--namespace','fixture','--actor','local']
    reject=subprocess.run(cmd,cwd=f.git,env=f.env,input='',text=True,capture_output=True,timeout=15)
    require(reject.returncode==1 and 'database schema is newer than this application' in reject.stderr,'v4 startup schema refusal')
    require(worker('get',mid)==before and worker('record')==r,'exact bytes, evidence and legacy receipt')
    ref,=before['references']; require(ref['commit_oid']==f.vcs('rev-parse','HEAD') and ref['object_oid']==f.vcs('rev-parse','HEAD:test.txt'),'Git proof')
    require([h['memory_id'] for h in worker('search')['hits']]==[mid],'search')
    restored=root/'restore'; snap=backup_store(path,restored)
    grant,_=f.mount(space,'pre-restore'); require(f.bridge(grant,'get',{'memory_id':mid})['result']['content']==before['content'],'pre-restore mount')
    with sqlite3.connect(f.catalog) as db:
        session,epoch=db.execute('SELECT session,epoch FROM sessions WHERE grant_id=?',
                                 (json.loads(grant.read_text())['grant_id'],)).fetchone()
    edit=worker('revise',mid,rid); worker('forget',mid,edit['revision_id']); require(not worker('search')['hits'],'forgotten')
    f.admin('store restore','restore',{'store_id':space['store_id'],'path':str(restored)})
    with sqlite3.connect(f.catalog) as db:
        require(db.execute('SELECT epoch,grant_id FROM sessions WHERE session=?',(session,)).fetchone()
                == (epoch+1,None),'restore invalidates old epoch')
    from nexus_memory.transport.harnesses.generic import resume
    from nexus_memory.domain.errors import GrantRevoked
    try: resume(f.catalog,grant)
    except GrantRevoked: pass
    else: raise ValueError('restore kept old authority')
    fresh,_=f.mount(space,'post-restore'); require(f.bridge(fresh,'get',{'memory_id':mid})['result']['content']==before['content'],'restored original, not later edits')
    return dict(old_source_sha=sha,schemas=[4,5,6],backup_blob_hash=original.row_hashes['blobs'],
                restored_durable_sequence=snap.last_durable_seq,old_startup_exit=reject.returncode,
                git_commit=ref['commit_oid'],git_blob=ref['object_oid'],legacy_receipt=r,
                assertions=['backup_verified','exact_get_search_retry_revise_forget','old_startup_refused',
                            'pre_restore_mount_revoked','later_edit_and_forget_absent'],outcome='passed')


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='nexus-drill-') as tmp:
        print(json.dumps(rehearse(Path(tmp)),indent=2))
