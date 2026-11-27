"""Grant-bound DSH NDJSON v1 endpoint. No protocol operation can select a mount."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from nexus_memory.domain.errors import InvalidInput, NexusError, OperationFailure
from nexus_memory.domain.models import MemoryInput
from nexus_memory.domain.spaces import OperationRef
from nexus_memory.storage.repository import WriteContext
from nexus_memory.transport.broker import Broker
from nexus_memory.transport.harnesses.generic import resume

LIMIT = 1 << 20


def require(value):
    if not value:
        raise InvalidInput('invalid bridge request')


class ProviderBroker(Broker):
    def _plan(self, name, args, handle):
        if name == 'capabilities':
            def capabilities(_):
                from nexus_memory.spaces.catalog import _reader
                with _reader(self.catalog.path) as db:
                    mode, = db.execute('SELECT mode FROM sessions WHERE grant_id=?', (self.grant.grant_id,)).fetchone()
                return {'search':True, 'get':True, 'remember':mode == 'read_write'}
            return capabilities
        if name in {'get', 'search'}:
            plan = super()._plan(name, args, handle)
            def visible(admission):
                from contextlib import closing
                def view(row):
                    value = asdict(row)
                    with closing(self.repo._connect()) as db:
                        metadata = db.execute('SELECT compat_metadata_json FROM revision_provenance WHERE '
                            'namespace=? AND actor=? AND memory_id=? AND revision_id=?',
                            (self.binding.scope.namespace,self.binding.scope.actor,row.memory_id,row.revision_id)).fetchone()
                    value['compat'] = json.loads(metadata[0]) if metadata and metadata[0] else {}
                    value['space_id'] = self.binding.space_id
                    return value
                result = plan(admission)
                if name == 'get':
                    return view(result)
                return {'hits':[dict(view(self.memory.get(hit.memory_id,hit.revision_id)),
                                     lexical_rank=hit.lexical_rank) for hit in result.hits]}
            return visible
        if name not in {'record', 'reconcile'}:
            return super()._plan(name, args, handle)
        key = args.pop('key')
        content = args.pop('content')
        require(not args.keys() - {'tags', 'category', 'importance', 'entities', 'source'})
        for field in ('entities', 'tags'):
            if field in args:
                values = args[field]
                require(isinstance(values, list) and len(values) <= 32 and all(
                    isinstance(s, str) and 0 < len(s.encode()) <= 256 for s in values))
        if 'category' in args:
            require(args['category'] in ('preference','decision','fact','insight','context','general'))
        if 'importance' in args:
            require(type(args['importance']) is int and 1 <= args['importance'] <= 5)
        if 'source' in args:
            require(args['source'] in ('user','agent','external'))
        context = WriteContext(self.binding.space_id, OperationRef(self.grant.writer_stream_id, key),
                               self.grant.principal_id, session_id=self.grant.session_id, compat_metadata=args)
        item = MemoryInput(content, 'decision' if args.get('category') == 'decision' else 'observation',
                           tuple(args.get('tags', ())))
        def execute(admission):
            prepared = self.memory.prepare_record(item, key, context)
            if name == 'record':
                return admission.exact(self.repo, prepared)
            if prepared.replay is None:
                return {'state':'absent_at_snapshot'}
            return {'state':'committed', 'receipt':asdict(prepared.replay)}
        return execute


def dispatch(broker, req):
    require(type(req) is dict and req.keys() >= {'v','id','method','params'} and
            type(req['v']) is int and req['v'] == 1 and type(req['params']) is dict)
    method, params = req['method'], req['params']
    keyed = method in ('remember', 'reconcile')
    require(set(req) == {'v','id','method','params'} | ({'operation_key'} if keyed else set()))
    if keyed:
        require('content' in params and 'key' not in params)
        params = dict(params, key=req['operation_key'])
    else:
        allowed = {'search':{'query','limit'}, 'get':{'memory_id'}, 'status':set(), 'capabilities':set()}
        require(method in allowed and not params.keys() - allowed[method])
    if method == 'search':
        require(isinstance(params.get('query'), str) and params['query'].strip())
    route = {'remember':'record'}.get(method,method)
    env = broker.dispatch(route,params)
    if isinstance(env, OperationFailure):
        if method == 'reconcile' and env.code not in ('invalid_input','idempotency_conflict'):
            return {'result':{'state':'unknown'}}
        return {'error':env.for_model()}
    try:
        return {'result':json.loads(broker.drain(env))}
    except NexusError:
        if method == 'reconcile':
            return {'result':{'state':'unknown'}}
        raise


def unique_object(pairs):
    obj = dict(pairs)
    require(len(obj) == len(pairs))
    return obj


def serve(broker, src, out):
    while line := src.readline(LIMIT + 1):
        if len(line) > LIMIT or not line.endswith(b'\n'):
            return 2
        ident = None
        try:
            req = json.loads(line.decode('utf-8'), object_pairs_hook=unique_object)
            require(type(req) is dict and isinstance(req.get('id'), str) and 0 < len(req['id'].encode()) <= 128)
            ident = req['id']
            reply = dispatch(broker,req)
        except (ValueError, KeyError, TypeError, RecursionError, NexusError):
            reply = {'error':{'code':'invalid_input'}}
        def encode(fields):
            return (json.dumps(dict(v=1,id=ident,ok='result' in fields,**fields),ensure_ascii=False)+'\n').encode()
        payload = encode(reply)
        out.write(payload if len(payload) <= LIMIT else encode({'error':{'code':'output_limit'}})); out.flush()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog',required=True,type=Path)
    parser.add_argument('--grant-file',required=True,type=Path)
    # A catalog repo session (Stage B): the launcher names the project and its verified checkout, and
    # a one-time grant minted into the catalog's sessions/ folder is deleted once read, as the MCP server does.
    parser.add_argument('--project-id')
    parser.add_argument('--repo',type=Path)
    parser.add_argument('--consume-grant-file',action='store_true')
    args = parser.parse_args()
    if (args.project_id is None) != (args.repo is None):
        parser.error('--project-id and --repo go together')
    try:
        try:
            broker = resume(args.catalog,args.grant_file,args.project_id,args.repo,broker_type=ProviderBroker)
        finally:
            if args.consume_grant_file and args.grant_file.parent == args.catalog.parent / 'sessions':
                args.grant_file.unlink(missing_ok=True)
        return serve(broker,sys.stdin.buffer,sys.stdout.buffer)
    except Exception:
        print('bridge unavailable',file=sys.stderr)
        return 1
