import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { openReceipt } from '../host.js';

const blocked = { status: 'unavailable' };
function fixture() {
  const id = 'session-A';
  const source = { kind: 'nexus-memory', operation: 'capture', outcome: 'success', form: 'notice',
    memoryId: 'memory-A', revisionId: 'rev-1', budgetTurn: 8,
    key: 'dsh-memory:' + createHash('sha256').update(`${id}:1`).digest('hex') };
  const message = { role: 'user', source, content: [{type:'text', text:'Ignored untrusted prose'}] };
  const events = [{ type:'turn/start', data:{turn:1} }, { type:'user/message', data:{role:'user', source:{kind:'user'}} },
    {type:'turn/end', data:{turn:1, reason:{kind:'completed'}}}, {type:'agent/inbox/spliced', data:{inserted:[message]}}]
    .map((e, seq) => ({...e, seq}));
  const session = { id, header:{id, isSeeded:false}, eventAt:n=>events[n], inheritedEventCount:0,
    isOwnSeq:n=>n >= session.inheritedEventCount && n < events.length };
  const agent = { id, session };
  const ctx = { agents:{get:k=>k === id ? agent : undefined, withInitiator:(a, f)=>{assert.equal(a, agent); return f();}},
    tools:{executable:(name, a)=>name === 'mcp__nexus__get' && a === agent} };
  const record = { memory_id:'memory-A', revision_id:'rev-1', current_revision_id:'rev-2',
    content:'  exact\r\n<untrusted> ☕\n', source_uri:`dsh-session:${id}/event/1`, extra:'private' };
  const calls = [], controller = new AbortController();
  const run = (seq = 3, transform = x => x) => openReceipt(ctx, agent, seq, controller.signal, async batch => {
    calls.push(batch); return transform({results:[{callId:batch.calls[0].id, result:{isError:false,value:{structuredContent:record}}}]});
  });
  return { ctx, agent, session, events, source, message, record, calls, run, controller };
}

test('one scoped, pinned get; exact saved bytes and original turn survive a later revision or retry', async () => {
  const f = fixture();
  const response = await f.run();
  assert.deepEqual(response, {status:'ok', memoryId:'memory-A', savedRevisionId:'rev-1', currentRevisionId:'rev-2',
    isCurrent:false, originSeq:1, turn:1, seq:3, content:f.record.content});
  assert.equal(f.calls.length, 1);
  const [batch] = f.calls;
  assert.equal(batch.agent, f.agent); assert.equal(batch.signal, f.controller.signal); assert.equal(batch.log, undefined);
  assert.equal(batch.calls.length, 1); assert.equal(batch.calls[0].name, 'mcp__nexus__get');
  assert.deepEqual(JSON.parse(batch.calls[0].arguments), {memory_id:'memory-A', revision_id:'rev-1'});
  f.record.current_revision_id = 'rev-1'; assert.equal((await f.run()).isCurrent, true);
});

test('only own, singleton Host success notices on the resolved live root authorize a read', async () => {
  for (const alter of [
    f=>{f.session.header.parentSession='parent';}, f=>{f.session.header.origin='subagent';},
    f=>{f.session.inheritedEventCount=4;}, f=>{f.session.header.delegationDepth=1;}, f=>{f.session.header.isSeeded=true;},
    f=>{f.ctx.agents.get=()=>({...f.agent});}, f=>{f.session.header.id='other';}, f=>{f.controller.abort();},
    f=>{f.ctx.tools.executable=()=>undefined;}, f=>{f.source.kind='user';}, f=>{f.source.form='recall';},
    f=>{f.source.operation='read';}, f=>{f.source.outcome='deduplicated';}, f=>{f.source.outcome='unknown';},
    f=>{f.message.role='assistant';}, f=>{f.events[3].data.inserted.push(f.message);},
    f=>{f.events[3].type='user/message'; f.events[3].data=f.message;}, f=>{f.events[3].type='tool/result';},
    f=>{f.source.memoryId='a\nb';}, f=>{f.source.revisionId='x'.repeat(129);}, f=>{delete f.source.key;},
  ]) { const f = fixture(); alter(f); assert.deepEqual(await f.run(), blocked, alter.toString()); assert.equal(f.calls.length, 0); }
  for (const seq of [null, {}, {memory_id:'memory-A',revision_id:'rev-1'}, [], '3', -1, 3.1, 999]) {
    const f = fixture(); assert.deepEqual(await f.run(seq), blocked); assert.equal(f.calls.length, 0);
  }
});

test('cross-session, key, origin, revision or identity mismatch cannot disclose a body', async () => {
  for (const alter of [
    f=>{f.record.memory_id='other';}, f=>{f.record.revision_id='rev-2';}, f=>{f.record.current_revision_id='';},
    f=>{f.record.source_uri='dsh-session:session-B/event/1';}, f=>{f.record.source_uri+='0';},
    f=>{f.source.key='dsh-memory:'+'a'.repeat(64);}, f=>{f.events[1].data.source.kind='nexus-memory';},
    f=>{f.events[0].type='turn/end';}, f=>{f.session.inheritedEventCount=2;}, f=>{f.record.content='x'.repeat(65537);},
  ]) { const f = fixture(); alter(f); assert.deepEqual(await f.run(), blocked, alter.toString()); }
});

test('tool errors, text-only results, late cancellation or revoked scope fail without leaking error data', async () => {
  for (const change of [r=>{r.results[0].result.isError=true;}, r=>{r.results[0].callId='wrong';},
    r=>{r.results.push(r.results[0]);}, r=>{const row=r.results[0]; row.result.content=[{type:'text',text:JSON.stringify(row.result.value)}]; delete row.result.value;},
    ()=>{throw Error('sensitive error');}, (_,f)=>f.controller.abort(), (_,f)=>{f.ctx.tools.executable=()=>false;},
    (_,f)=>{f.ctx.agents.get=()=>undefined;},
  ]) { const f=fixture(); assert.deepEqual(await f.run(3, r=>{change(r,f);return r;}), blocked); }
});

test('successful verification origin must be a non-error tool result', async () => {
  const f = fixture(); f.events[1]={seq:1,type:'tool/result',data:{turn:5,message:{isError:false}}};
  assert.equal((await f.run()).turn, 5); f.events[1].data.message.isError=true; assert.deepEqual(await f.run(), blocked);
});
