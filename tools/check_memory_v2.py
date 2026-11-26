"""Stage A synthetic protocol gate. Never accepts a store, grant, or profile path."""
import argparse
import asyncio
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

from mcp import Client, StdioServerParameters
from nexus_memory.domain.graph import (Applicability, AssertionDraft, CodeSource, CurationBatch,
    EntityDraft, EntityRef, EvidenceInput, MemorySource, Span, Validity)
from nexus_memory.domain.spaces import MemoryRef

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(sys.executable).parent
QUERY = {'query': {'anchors': ['renew_lease'], 'intent': 'compound'}}


def require(condition, label):
    if not condition:
        raise ValueError(label)


class Fixture:
    def __init__(self, root):
        self.root = root
        self.git = root / 'checkout'
        self.git.mkdir()
        self.catalog = root / 'catalog'
        self.env = {'PATH': os.environ['PATH'], 'HOME': str(root), 'GIT_CONFIG_GLOBAL': os.devnull,
                    'GIT_CONFIG_NOSYSTEM': '1', 'LANG': 'en_US.UTF-8'}

    def run(self, *cmd, data=None):
        return subprocess.run(list(map(str, cmd)), input=data, text=True, capture_output=True,
                              cwd=self.git, env=self.env, timeout=30, check=True).stdout.strip()

    def vcs(self, *args):
        return self.run('git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', *args)

    def admin(self, command, key, body=None, *extra):
        args = ['--catalog', self.catalog, '--operation-key', key, *extra]
        if body is not None:
            request = self.root / 'request.json'
            request.write_text(json.dumps(body))
            args += ['--request', request]
        return json.loads(self.run(BIN / 'nexus-memory-admin', *command.split(), *args))

    def space(self, name, project=False):
        body = {'name': name, 'kind': 'project' if project else 'research'}
        if project:
            body['repo'] = str(self.git)
        space = self.admin('space create', name, body)
        self.admin('store migrate', 'migrate-'+name, {'store_id':space['store_id']})
        return space

    def mount(self, space, name, mode='read_only'):
        ticket = self.root / name
        handle = self.admin('grant issue', 'issue-'+name, {'space_id':space['space_id'],
            'principal_id':'local','receiving_context':name,'mode':mode}, '--out', ticket)
        return ticket, handle

    def bridge(self, ticket, method, params, **extra):
        frame = dict(v=1, id='fixture', method=method, params=params, **extra)
        return json.loads(self.run(BIN/'nexus-memory-bridge', '--catalog', self.catalog,
            '--grant-file', ticket, data=json.dumps(frame)+'\n'))

    def curate(self, sid, key, batch):
        grant = self.root / (key+'.approval')
        preview = dict(family='graph curate', operation_key=key, space_id=sid,
                       exposed_space_ids=[sid], request=batch)
        self.admin('grant issue', 'approve-'+key, preview, '--out', grant)
        return self.admin('graph curate', key, batch, '--space-id', sid, '--grant-file', grant)

    def server_args(self, ticket):
        return ['--catalog', str(self.catalog), '--grant-file', str(ticket), '--repo', str(self.git)]

    def client(self, ticket):
        return Client(StdioServerParameters(command=str(BIN/'nexus-memory'), args=self.server_args(ticket),
                                            cwd=self.git, env=self.env))


async def tool(client, name, args, error=None):
    reply = await client.call_tool(name,args)
    if error:
        require(reply.is_error and reply.structured_content['code'] == error, name+' denial')
        return
    require(not reply.is_error, name+' success')
    return json.loads(reply.content[0].text)


async def scenario(f, fmt, host_probe=None):
    f.vcs('init','-q','-b','release','--object-format='+fmt)
    filename, proof = 'test.txt', 'test_rejoin_after_suspend: café'
    (f.git/filename).write_text(proof)
    f.vcs('add',filename); f.vcs('commit','-qm','release')
    R, blob = f.vcs('rev-parse','HEAD'), f.vcs('rev-parse','HEAD:'+filename)
    f.vcs('switch','-qc','main'); f.vcs('commit','--allow-empty','-qm','main')
    M = f.vcs('rev-parse','HEAD'); f.vcs('switch','-q','release')
    f.run(sys.executable,'-I',ROOT/'integrations/project_scope/launcher.py','enroll',f.git)
    f.admin('catalog init','init')
    p, x = f.space('P',True), f.space('X')
    sid = p['space_id']; repo = p['repository_bindings'][0]['repository_id']
    # A project space starts recall-only: no writable grant until the owner raises its ceiling.
    try:
        f.mount(p,'too-early','read_write')
        raise SystemExit('FAIL: a recall-only project space issued a writable grant')
    except subprocess.CalledProcessError:
        pass
    f.admin('space ceiling','maintain-P',{'space_id':sid,'ceiling':'maintain'})
    hidden, _ = f.mount(x,'hidden','read_write')
    canary = 'unmounted-synthetic-canary'
    secret = f.bridge(hidden,'remember',{'content':canary},operation_key='x')['result']
    writer, wh = f.mount(p,'writer','read_write')
    reader, rh = f.mount(p,'receiver')
    async with f.client(reader) as client:
        require((await tool(client,'status',{}))['active_memories'] == 0,'empty P')
    nodes = ('renew_lease','D17','I42','test_rejoin_after_suspend',filename,'D32')
    text = 'D17 retains a 30-second grace period because a suspended worker may rejoin.'
    drafts, evidence, writes = [], [], {}
    for key,s,pred,o,body,commit in [ ('implements',0,'IMPLEMENTS',1,text,R),
        ('motivation',1,'MOTIVATED_BY',2,'I42: a suspended worker may rejoin.',R),
        ('regression',3,'REGRESSION_FOR',2,'Rejoin regression guards I42.',R),
        ('file',3,'DEFINED_IN',4,proof,R),('main',0,'IMPLEMENTS',5,'D32 removes the grace period.',M)]:
        if key=='file':
            source = CodeSource(repo,R,filename,blob)
        else:
            r = writes[key] = f.bridge(writer,'remember',{'content':body},operation_key=key)['result']
            source = MemorySource(MemoryRef(sid,r['memory_id'],r['revision_id']))
        data = body.encode(); group = key+'-source'
        evidence.append(EvidenceInput(key,'support',source,Span(0,len(data),hashlib.sha256(data).hexdigest()),group))
        drafts.append(AssertionDraft(EntityRef(nodes[s]),pred,EntityRef(nodes[o]),temp_id=key,
            applicability=Applicability('commit_set',repo,(commit,),(group,)),
            validity=Validity.unbounded(),support_groups=((group,),)))
    batch=CurationBatch(tuple(EntityDraft(k,'fixture',k) for k in nodes),tuple(drafts),tuple(evidence))
    graph = f.curate(sid,'graph',json.loads(batch.to_json()))
    exact = writes['implements']; require(exact['operation_id'] != graph['operation_id'],'separate receipts')
    forbidden = (canary,x['space_id'],x['store_id'],secret['memory_id'],secret['revision_id'])
    def verify(pack):
        require(pack['status']=='supported' and pack['proof_edges']==1,'supported with file proof')
        bundle, = pack['bundles']; edges = bundle['path']
        require([(e['step']['predicate'],e['step']['direction']) for e in edges] == [
            ('IMPLEMENTS','forward'),('MOTIVATED_BY','forward'),('REGRESSION_FOR','inverse'),('DEFINED_IN','forward')], 'path')
        require([e['claim']['revision_id'] for e in edges]==[r['revision_id'] for r in graph['assertion_revisions'][:4]],'claim revisions')
        citations = [{'source':c['source'],'span':c['span']} for c in bundle['citations']]
        expected = [json.loads(e.to_json()) for e in evidence[:4]]
        require(len(citations)==4 and all({'source':e['source'],'span':e['span']} in citations for e in expected),'exact sources and digests')
        require(all(s not in json.dumps(pack) for s in forbidden),'no X disclosure')
        return citations
    if host_probe:
        host_probe(f,reader,verify)
    async with f.client(reader) as client:
        pack = await tool(client,'recall',QUERY); citations=verify(pack)
        require((await tool(client,'get',{'memory_id':exact['memory_id']}))['content']==text,'exact bytes')
        require(not (await tool(client,'search',{'query':canary}))['hits'],'hidden hits')
        f.curate(sid,'withdraw',{'withdrawals':[{'evidence_id':graph['id_mappings']['motivation-source'],'expected_head_event_seq':graph['graph_event_seq']}]})
        unresolved = await tool(client,'recall',QUERY)
        require(unresolved['status']=='no_supported_path' and not unresolved['bundles'],'withdrawal')
        async with f.client(writer) as w:
            await tool(w,'revise',{'memory_id':exact['memory_id'],'expected_revision_id':exact['revision_id'],
                                   'content':'corrected fixture','idempotency_key':'correction'})
        await tool(client,'explain',{'pack_id':unresolved['pack_id']},'stale_pack')
        one = await tool(client,'traverse',{'query':{'anchors':['renew_lease'],'pattern':[{'predicate':'IMPLEMENTS','direction':'forward'}]}})
        require(not one['bundles'],'no corrected support')
        f.admin('grant revoke','revoke-reader',{'grant_id':rh['grant_id']})
        await tool(client,'get',{'memory_id':exact['memory_id']},'grant_revoked')
        await tool(client,'explain',{'pack_id':one['pack_id']},'grant_revoked')
    import select
    with subprocess.Popen([BIN/'nexus-memory-bridge','--catalog',f.catalog,'--grant-file',writer],
                          cwd=f.git, env=f.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as bridge:
        def call(method, params, **extra):
            bridge.stdin.write(json.dumps(dict(v=1,id='check',method=method,params=params,**extra)).encode()+b'\n')
            bridge.stdin.flush()
            require(select.select([bridge.stdout],[],[],10)[0],'bridge timed out')
            return json.loads(bridge.stdout.readline())
        try:
            require(call('capabilities',{})['result']['remember'],'writer mounted')
            f.admin('grant revoke','revoke-writer',{'grant_id':wh['grant_id']})
            for key in ('implements','new'):
                denied=call('remember',{'content':text},operation_key=key)
                require(denied.get('error',{}).get('code')=='grant_revoked','revoked replay/write')
        finally:
            bridge.stdin.close(); bridge.kill()
    return dict(R=R,M=M,exact_receipt=exact,curation_receipt=graph,retrieved_refs=citations,
                assertions=['empty_P','three_semantic_hops','one_file_proof','exact_sources','hidden_X',
                            'withdrawal_unresolved','correction_stale','revoked_read_explain_replay_write'])


def check(fmt):
    with tempfile.TemporaryDirectory(prefix='nexus-a13-') as d:
        return asyncio.run(scenario(Fixture(Path(d)),fmt))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--object-format',choices=('sha1','sha256'),default='sha1')
    args=parser.parse_args()
    report=dict(source_sha=subprocess.check_output(['git','-C',ROOT,'rev-parse','HEAD'],text=True).strip(),
        working_tree_modified=bool(subprocess.check_output(['git','-C',ROOT,'status','--porcelain'])),
        runtime=dict(os=platform.system(),arch=platform.machine(),python=platform.python_version(),
                     sqlite=sqlite3.sqlite_version,mcp=version('mcp')),kind='synthetic_protocol_fixture',
        hosts={name:'not exercised by this checker' for name in ['DSH','Claude Code','Codex']},
        limitations=['not host certification','no model or utility evaluation','not a release authorization'])
    try:
        report.update(check(args.object_format),outcome='passed')
    except Exception as exc:
        report.update(outcome='failed',error_type=type(exc).__name__)
    print(json.dumps(report,indent=2)); return int(report['outcome']!='passed')


if __name__=='__main__':
    raise SystemExit(main())
