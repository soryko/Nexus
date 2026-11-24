import { createHash } from 'node:crypto';

const MAX_ORIGINS = 256;
const MAX_TURNS = 32;
const PER_TURN = 8;
const keyPattern = /^dsh-memory:[0-9a-f]{64}$/;
const index = v => Number.isSafeInteger(v) && v >= 0;
const turnNumber = v => index(v) && v > 0;
const identity = v => typeof v === 'string' && v.length >= 1 && v.length <= 128 && !/[^A-Za-z0-9_.:-]/.test(v);

export function init(header, inheritedEventCount) {
  const root = !header.parentSession && !header.isSeeded && !header.origin && !(header.delegationDepth ?? 0)
    && inheritedEventCount === 0;
  return { sessionId: root ? header.id : null, turn: null, origins: [], view: { turns: {} } };
}

// Fold committed metadata only. The hash index lets a late confirmation resolve its
// original event without opening Nexus or retaining a message, result, or draft.
export function apply(state, event) {
  if (!state.sessionId || !index(event.seq)) return state;
  const data = event.data;
  if (event.type === 'turn/start' || event.type === 'turn/end') {
    const turn = event.type === 'turn/start' && turnNumber(data?.turn) ? data.turn : null;
    return state.turn === turn ? state : { ...state, turn };
  }
  if (event.type === 'user/message' || event.type === 'tool/result') {
    const originTurn = event.type === 'user/message'
      ? data?.role === 'user' && data.source?.kind === 'user' && state.turn
      : data?.message?.isError === false && !data.error && data.turn;
    if (!turnNumber(originTurn)) return state;
    const key = 'dsh-memory:' + createHash('sha256').update(`${state.sessionId}:${event.seq}`).digest('hex');
    return { ...state, origins: [...state.origins.slice(1 - MAX_ORIGINS), { key, turn: originTurn }] };
  }
  if (event.type !== 'agent/inbox/spliced') return state;
  const inserted = data?.inserted;
  if (!Array.isArray(inserted) || inserted.length !== 1 || inserted[0]?.role !== 'user') return state;
  const src = inserted[0].source;
  if (src?.kind !== 'nexus-memory' || src.form !== 'notice' || src.operation !== 'capture' || src.outcome !== 'success'
      || !identity(src.memoryId) || !identity(src.revisionId) || typeof src.key !== 'string'
      || src.key.length !== 75 || !keyPattern.test(src.key)) return state;
  const origin = state.origins.find(row => row.key === src.key);
  if (!origin) return state;
  const turns = { ...state.view.turns };
  turns[origin.turn] = [...(turns[origin.turn] ?? []).slice(1 - PER_TURN), { seq: event.seq }];
  const keys = Object.keys(turns).map(Number);
  if (keys.length > MAX_TURNS) delete turns[Math.min(...keys)];
  // Consume once: a duplicate success for the same capture must not add a second cue.
  return { ...state, origins: state.origins.filter(row => row !== origin), view: { turns } };
}

export function receiptProjection(z) {
  const seq = z.int().nonnegative();
  const turn = z.int().positive();
  const viewSchema = z.strictObject({ turns: z.record(z.string().regex(/^[1-9]\d*$/),
    z.array(z.strictObject({ seq })).max(PER_TURN)).refine(v => Object.keys(v).length <= MAX_TURNS) });
  return { key: 'nexusInspectorReceipts', stateVersion: 1, init, apply,
    stateSchema: z.strictObject({ sessionId: z.string().nullable(), turn: turn.nullable(),
      origins: z.array(z.strictObject({ key: z.string().length(75).regex(keyPattern), turn })).max(MAX_ORIGINS), view: viewSchema }),
    wire: { summary: false, view: state => state.view, viewSchema } };
}
