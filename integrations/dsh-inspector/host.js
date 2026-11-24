import { createHash, randomUUID } from 'node:crypto';
import { createInspectorApi } from './descriptor.js';

export const name = 'nexus-receipt-inspector';
export const inject = ['agents', 'tools', 'typert', 'sessionProjections'];
const GET = 'mcp__nexus__get';
const unavailable = () => ({ status: 'unavailable' });
const identity = value => typeof value === 'string' && value.length >= 1 && value.length <= 128 && !/[^A-Za-z0-9_.:-]/.test(value);
const index = value => Number.isSafeInteger(value) && value >= 0;

// The lookup parameter is resolved by DSH's gateway *after* peer/session admission.
// Never resolve a memory ID, revision ID, tool name, or store from a caller's arguments.
function confirmedReceipt(session, seq) {
  if (!index(seq) || !session.isOwnSeq(seq)) return;
  const event = session.eventAt(seq);
  if (event?.seq !== seq || event.type !== 'agent/inbox/spliced') return;
  const inserted = event.data.inserted;
  // hooks.js calls agent.inject(oneNotice). Don't guess an occurrence in a batch.
  if (!Array.isArray(inserted) || inserted.length !== 1 || inserted[0]?.role !== 'user') return;
  const src = inserted[0].source;
  if (src?.kind !== 'nexus-memory' || src.form !== 'notice' || src.operation !== 'capture' || src.outcome !== 'success'
      || !identity(src.memoryId) || !identity(src.revisionId) || typeof src.key !== 'string'
      || src.key.length !== 75 || !/^dsh-memory:[0-9a-f]{64}$/.test(src.key)) return;
  return src;
}

function originOf(session, receiptSeq, key, uri) {
  const prefix = `dsh-session:${session.id}/event/`;
  if (typeof uri !== 'string' || !uri.startsWith(prefix)) return;
  const suffix = uri.slice(prefix.length);
  const seq = Number(suffix);
  if (!index(seq) || String(seq) !== suffix || seq >= receiptSeq || !session.isOwnSeq(seq)) return;
  if (key !== 'dsh-memory:' + createHash('sha256').update(`${session.id}:${seq}`).digest('hex')) return;
  const event = session.eventAt(seq);
  if (event?.seq !== seq) return;
  if (event.type === 'tool/result' && event.data.message?.isError === false && !event.data.error
      && index(event.data.turn) && event.data.turn > 0) return { originSeq: seq, turn: event.data.turn };
  if (event.type !== 'user/message' || event.data?.role !== 'user' || event.data.source?.kind !== 'user') return;
  // A retry can be confirmed in a later turn. Find the original turn, not the notice's budgetTurn.
  for (let i = seq - 1; i >= session.inheritedEventCount; i--) {
    const prior = session.eventAt(i);
    if (!prior || prior.type === 'turn/end') return;
    if (prior.type === 'turn/start') {
      if (index(prior.data.turn) && prior.data.turn > 0) return { originSeq: seq, turn: prior.data.turn };
      return;
    }
  }
}

/** Internal test seam: executeCapability is the Host-owned native batch dispatcher, never an RPC argument. */
export async function openReceipt(ctx, agent, seq, signal, executeCapability) {
  try {
    const session = agent?.session;
    const currentScope = () => session && agent.session === session && agent.id === session.id
      && session.header.id === agent.id && ctx.agents.get(agent.id) === agent
      && !session.header.parentSession && !session.header.isSeeded && !session.header.origin
      && (session.header.delegationDepth ?? 0) === 0 && !signal.aborted;
    if (!currentScope()) return unavailable();
    const receipt = confirmedReceipt(session, seq);
    if (!receipt || !ctx.tools.executable(GET, agent)) return unavailable();
    const id = randomUUID();
    const result = await ctx.agents.withInitiator(agent, () => executeCapability({ agent, signal,
      calls: [{ type: 'tool-call', id, name: GET, arguments: JSON.stringify({ memory_id: receipt.memoryId, revision_id: receipt.revisionId }) }],
    }));
    if (!currentScope() || !ctx.tools.executable(GET, agent) || result.results.length !== 1) return unavailable();
    const row = result.results[0];
    const record = row.result?.value?.structuredContent;
    if (row.callId !== id || row.result?.isError !== false || record?.memory_id !== receipt.memoryId
        || record.revision_id !== receipt.revisionId || !identity(record.current_revision_id)
        || typeof record.content !== 'string' || Buffer.byteLength(record.content) > 65536) return unavailable();
    const origin = originOf(session, seq, receipt.key, record.source_uri);
    if (!origin) return unavailable();
    return { status: 'ok', seq, ...origin, memoryId: record.memory_id, savedRevisionId: record.revision_id,
      currentRevisionId: record.current_revision_id, isCurrent: record.revision_id === record.current_revision_id, content: record.content };
  } catch {
    // Not found, inaccessible, malformed and unavailable are deliberately indistinguishable.
    return unavailable();
  }
}

export async function apply(ctx) {
  const [{ TypertRemoteService }, { NativeToolExecutor }] = await Promise.all([
    import('@deepseek-ai/dsh-typert-protocol'), import('@deepseek-ai/dsh-tool-executor-native'),
  ]);
  const [{ receiptProjection }, { z }] = await Promise.all([import('./projection.js'), import('zod')]);
  await ctx.sessionProjections.register(receiptProjection(z)); // Registration belongs to this plugin's fiber.
  const executor = new NativeToolExecutor(ctx, () => 1);
  const closing = new AbortController();
  const tasks = new Set();
  ctx.effect(() => async () => { closing.abort(); await Promise.allSettled([...tasks]); });
  class Inspector extends TypertRemoteService {
    constructor() { super(ctx, 'nexusInspector'); }
    async open(agent, seq, signal) {
      // Don't implicitly grant record bodies to viewers who only have chat access.
      if (this.ctx.invocation?.peer?.principal?.kind !== 'owner') return unavailable();
      const active = AbortSignal.any([signal, closing.signal, AbortSignal.timeout(10000)]);
      const task = openReceipt(ctx, agent, seq, active, batch => executor.execute(batch));
      tasks.add(task);
      try { return await task; } finally { tasks.delete(task); }
    }
  }
  new Inspector();
  const inspectorApi = createInspectorApi(z);
  ctx.typert.register({ package: inspectorApi.package, face: 'host', schemas: [],
    model: { services: [], events: [], objects: [] }, invocations: inspectorApi.descriptors });
}
