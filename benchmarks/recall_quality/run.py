"""Run a zero-provider-call, fixture-only paired recall experiment."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from fixture import HERE, Fixture, Unsafe, digest
from policy import admit
from score import summarize


def load(name):
    return json.loads((HERE / name).read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    manifest = load('manifest.json')
    for name, expected in manifest['sha256'].items():
        if sha(HERE / name) != expected:
            raise ValueError(f'frozen input changed: {name}')
    return manifest


def replay(case, responses, manifest):
    payload = {'cases': [{'id': case['id'], 'prompt': case['prompt'], 'responses': responses}]}
    env = {k: os.environ[k] for k in ('PATH', 'DSH_PROFILE_DIR', 'NEXUS_RECALL_PACKAGE_DIR') if k in os.environ}
    proc = subprocess.run(['node', str(HERE/'route.mjs')], input=json.dumps(payload), text=True,
                          env=env, capture_output=True, timeout=15, check=True)
    result = json.loads(proc.stdout)
    if result['route']['config_sha256'] != manifest['route_config_sha256']:
        raise ValueError('route config mismatch')
    return result['cases'][0]


def timed(log, name, args, fn):
    start = perf_counter_ns()
    event = {'name':name, 'arguments': args, 'tokens':None, 'status':'error'}
    log.append(event)
    try:
        result = fn()
        event['status'] = 'success'
        return result
    finally:
        event['latency_ms'] = (perf_counter_ns()-start)/1e6


def pair(case, f, manifest):
    # Probe callback control flow only; absent fixture responses are never scored.
    probe = replay(case, [], manifest)
    queries = [c['arguments'] for c in probe['calls'] if c['name']=='mcp__nexus__search']
    if len(probe['calls']) != len(queries) or len(queries)>1:
        raise ValueError('unexpected route search')
    controls = queries + [{'query': 'OLDORCHID OR BLUEQUARTZ', 'advanced':True}, {'query':'Lumen','limit':100}]
    before = f.controls(controls)
    rows, packets = [], []
    for arm in ('A','B'):
        log, bodies, reads = [], {}, []
        page = (timed(log, 'search', queries[0], lambda: f.search(queries[0])) if queries else {'hits':[]})
        hits = page['hits']
        ranked = [f.labels[h['memory_id']] for h in hits]
        if arm == 'A':
            # Get plan directly from pinned callback, then verify each actual call in the final replay.
            # Discovery supplies successful shape-only placeholders: on a failed get the
            # installed callback may stop before the next one. Never score probe output.
            placeholders = [dict(memory_id=h['memory_id'], revision_id=h['revision_id'],
                                 current_revision_id=h['revision_id'], content='fixture') for h in hits[:2]]
            plan = replay(case, [page, *placeholders] if queries else [], manifest)['calls'][len(queries):]
            if any(c['name']!='mcp__nexus__get' for c in plan) or len(plan)>2:
                raise ValueError('unexpected route get')
        else:
            if ranked != rows[0]['hit_ids']:
                raise Unsafe('ranked search changed between arms')
        responses = [page] if queries else []
        for call in plan:
            args = call['arguments']
            f.guard(args)
            hit = next(h for h in hits if all(h[k]==v for k,v in args.items()))
            if arm=='B' and not admit(queries[0]['query'], hit['excerpt']):
                continue
            label = f.labels[args['memory_id']]
            body = timed(log, 'get', {'label':label, 'revision': f.revisions[label].index(args['revision_id'])+1},
                         lambda: asdict(f.service.get(**args)))
            f.guard(body)
            reads.append(label)
            responses.append(body)
            bodies[label] = body['content']
        if arm == 'A':
            trace = replay(case, responses, manifest)
            f.guard(trace)
            if not trace['complete'] or trace['calls'] != probe['calls']+plan:
                raise ValueError('incomplete callback replay')
            offered = [f.labels[mid] for mid in trace['candidate_ids']]
            if any(bodies[label] not in trace['injection'] for label in offered):
                raise ValueError('normalized packet would exceed delivered body text')
        else:
            # Same renderer eligibility as A: no backfill and only previously admitted current bodies.
            offered = [label for label in reads if label in rows[0]['offered_ids']]
        evidence = [{'label':label, 'text':bodies[label]} for label in offered]
        if len(json.dumps(evidence).encode()) > manifest['answer_evidence_bytes']:
            raise ValueError('answer evidence cap exceeded')
        after = f.controls(controls)
        if before != after:
            raise Unsafe('explicit search, exact read or generation changed')
        blind = secrets.token_hex(12)
        rows.append({'case':case['id'],'class':case['class'],'arm':arm,'blind_id':blind,
                     'query_method':'installed local terms','query':queries,'hit_ids':ranked,
                     'offered_ids':offered,'body_read_ids':reads,'complete':True,'safety':'pass',
                     'controls_before':before,'controls_after':after,'calls':log,
                     'observed_answer':None,'observed_citation':None})
        packets.append({'id':blind,'prompt':case['prompt'],'instructions':manifest['answer_instructions'],
                        'evidence':evidence})
    return rows, packets


def run(output):
    manifest = verify()
    output.mkdir(parents=True, exist_ok=False)
    rows, packets = [], []
    records, cases = load('corpus.json'), load('cases.json')
    with TemporaryDirectory(prefix='recall-pilot-') as scratch:
        fixture = Fixture(scratch, records)
        for case in cases:
            try:
                pair_rows, pair_packets = pair(case, fixture, manifest)
            except (Unsafe, ValueError, subprocess.SubprocessError, OSError) as exc:
                for arm in ('A','B'):
                    rows.append({'case':case['id'],'class':case['class'],'arm':arm,'complete':False,
                                 'safety':'fail' if isinstance(exc, Unsafe) else 'unknown','error':type(exc).__name__})
                # Route identity failure must not be followed by a different route.
                if isinstance(exc, (Unsafe, subprocess.SubprocessError, OSError)):
                    break
            else:
                rows.extend(pair_rows)
                packets.extend(pair_packets)
    key = load('key.json')
    for row in rows:
        row.update(config_hash=sha(HERE/'manifest.json'), expected=key[row['case']])
    receipts = {r['id'] for r in records if r['receipt']}
    summary = summarize(rows, key, receipts)
    for name, obj in [('receipts',rows), ('blind-packets',sorted(packets,key=lambda p:p['id'])), ('summary',summary)]:
        (output/(name+'.json')).write_text(json.dumps(obj,indent=2)+'\n')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('new_output_directory',type=Path)
    summary=run(parser.parse_args().new_output_directory)
    print(json.dumps(summary, indent=2))
    raise SystemExit(2 if summary['incomplete_pairs'] or summary['quiet_gate']=='disqualified' else 0)
