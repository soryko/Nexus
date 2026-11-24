// Replay only the hash-pinned installed pre-step callback. No Host or MCP client.
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const digest = data => createHash('sha256').update(data).digest('hex')
const pins = {
  'package.json': 'd180da88cdba89cae8a699348e825453f340d16804ec544d5f4ca8f8a58dfbb4',
  'hooks.js': '45e581ff376de3430beb7f0a588fcbdf84b9b48f8a64ae421cc3493583455566',
  'hooks-core.js': '7b37614b3d3916f3424c383318451cd8d82aecb63c5515d8508e38b9e41f11c8',
}

export async function loadRoute(dir = process.env.NEXUS_RECALL_PACKAGE_DIR ||
  (process.env.DSH_PROFILE_DIR && join(process.env.DSH_PROFILE_DIR, 'node_modules/@local/dsh-nexus-memory'))) {
  if (!dir) throw new Error('Set NEXUS_RECALL_PACKAGE_DIR to the packageDir verified by Config.listConfigs')
  for (const [file, expected] of Object.entries(pins)) {
    if (digest(readFileSync(join(dir, file))) !== expected) throw new Error('Installed route changed: ' + file)
  }
  const manifest = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'))
  const hooks = await import(pathToFileURL(join(dir, manifest.exports['./hooks'])))
  // The installed bundle's hook row specifies exactly these package defaults.
  const config = hooks.Config.parse({})
  return { hooks, config, identity: { entry: 'include:nexus-memory-hooks', version: manifest.version,
    sha256: pins, config_sha256: digest(JSON.stringify(config)) } }
}

export async function replayCase({ hooks, config }, { id, prompt, responses }) {
  if (typeof id !== 'string' || typeof prompt !== 'string' || !Array.isArray(responses)) throw new Error('Invalid fixture case')
  const calls = [], listeners = new Map(), cleanup = [], warnings = []
  const agent = { id: 'fixture-agent', session: {}, ctx: { effect: setup => setup() } }
  const ctx = {
    effect: setup => cleanup.push(setup()), on: (name, fn) => listeners.set(name, fn),
    systemPrompt: { section() {} }, logger: { warn: text => warnings.push(text) },
    agents: { withInitiator: (_agent, fn) => fn() },
    sessionProjections: { register(projection) {
      const state = projection.apply(projection.init({}, 0), { seq: 0, type: 'turn/start', data: { turn: 1 } })
      return { stateOf: () => state, dispose() {} }
    } },
  }
  const dispatch = ({ calls: batch }) => {
    const [call] = batch
    const response = responses[calls.length]
    calls.push({ name: call.name, arguments: JSON.parse(call.arguments) })
    if (batch.length !== 1 || ![config.searchTool, config.getTool].includes(call.name)) throw new Error('Unexpected capability')
    // No fallback: absence is an error reply, not a synthetic empty result.
    const result = response == null ? { isError: true } : { value: { structuredContent: response } }
    return { results: [{ callId: call.id, result }] }
  }
  await hooks.installHooks(ctx, config, dispatch)
  try {
    const messages = [{ role: 'user', source: { kind: 'user' }, content: [{ type: 'text', text: prompt }] }]
    const decision = await listeners.get('agent/pre-step')({ agent, turn: 1, messages,
      signal: new AbortController().signal }, () => ({ kind: 'enter', messages }))
    const message = decision.messages.find(m => m.source.kind === 'nexus-memory')
    return { id, complete: warnings.length === 0 && calls.length === responses.length &&
      (calls.length === 0 || message?.source.failed === false), calls,
      injection: message?.content[0].text ?? '', candidate_ids: message?.source.memories.map(m => m.memory_id) ?? [],
      receipt: message?.source ?? null }
  } finally {
    for (const dispose of cleanup.reverse()) await dispose()
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const { cases } = JSON.parse(readFileSync(0, 'utf8'))
  if (!Array.isArray(cases)) throw new Error('Expected {cases:[...]}')
  const route = await loadRoute()
  const rows = []
  for (const fixture of cases) rows.push(await replayCase(route, fixture))
  console.log(JSON.stringify({ route: route.identity, cases: rows }))
}
