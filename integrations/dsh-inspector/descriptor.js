// Canonical contract. The browser receives this same factory inline (build-client.mjs).
// Only the gateway resolves agentId; no record or revision ID is accepted as input.
export function createInspectorApi(z) {
  const pkg = '@local/dsh-nexus-inspector';
  const strict = (symbol, schema, owner = pkg) => ({ mode: 'strict', typeSymbol: `${owner}#${symbol}`, create: () => schema });
  const index = z.int().nonnegative();
  const id = z.string().min(1).max(128).regex(/^[A-Za-z0-9_.:-]+$/);
  const result = z.discriminatedUnion('status', [
    z.strictObject({ status: z.literal('unavailable') }),
    z.strictObject({
      status: z.literal('ok'), seq: index, originSeq: index, turn: z.int().positive(),
      memoryId: id, savedRevisionId: id, currentRevisionId: id, isCurrent: z.boolean(),
      content: z.string().max(65536),
    }),
  ]);
  return { package: pkg, descriptors: [{
    id: `${pkg}#nexusInspector/open`, service: 'nexusInspector', namespace: 'nexusInspector', method: 'open',
    invocation: { kind: 'direct' }, parameters: [
      { name: 'agent', wire: 'agentId', source: 'lookup', lookup: 'agent', codec: strict('SessionId', z.string().min(1), '@deepseek-ai/dsh-session/types') },
      { name: 'seq', wire: 'seq', source: 'json', codec: strict('ReceiptSeq', index) },
    ], cancellation: { parameter: 'signal' }, result: strict('OpenResult', result),
  }] };
}
