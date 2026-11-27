/** The audited DSH host for Nexus (Stage B, Phase 2).
 *
 * Reads and writes take separate paths, each on its own grant:
 *
 * - Recall goes through mnemon. This plugin composes the only Memory Spaces Source that carries
 *   the Nexus child, on a read-only (delegated) grant, and refuses every mutation through it.
 *   mnemon's own remember delegates to a spawned child session, and the agent a Source request
 *   names is not a reliable identity, so no write may arrive that way (first real save, 27 Nov).
 * - Saving goes through the `nexus_remember` tool, on a root grant. A tool call carries the
 *   agent that is actually executing it, so the root check reads that agent's own session header.
 *
 * The host vouches only while:
 * - One workspace. The process runs in the configured workspace, mounts no multi-workspace
 *   registry, and every Source request names that workspace.
 * - One exposure. No other loader entry names the Nexus provider, bridge or launcher.
 * - Pinned. Hot reload is off. Each save re-checks the whole loader inventory; the first save
 *   fixes its digest, and any later change refuses saves until DSH restarts.
 * The Nexus catalog enforces the repo's mode and the root role again on its side.
 */
import {createHash} from 'node:crypto'
import {realpathSync} from 'node:fs'
import {resolve} from 'node:path'
import {installMemorySpaces} from 'dsh-mnemon-source-memory-spaces'
import readOnlyProvider from './index.js'
import type {Binding} from './client.js'
import {RootWriter,rememberInput} from './root-writer.js'

export const name = 'dsh-nexus-writer-host'
export const inject = ['mnemonMemory', 'loader', 'tools']

export interface HostConfig {
  /** Stable Source instance id for this composed Source. */
  instanceId: string
  /** Absolute path of the one workspace (a Git checkout) this process serves. */
  workspace: string
  /** The save path: the project launcher's `dsh` command, which mints a root session grant. */
  binding: Binding & {journalDir: string}
  /** The recall path: the launcher's `dsh-worker` command, which mints a read-only grant. */
  readBinding: Binding
  /** Memory Spaces Source settings (dataDir and the like), passed through unchanged. */
  memorySpaces?: Record<string, unknown>
}

export const TOOL = 'nexus_remember'
const NEXUS = /dsh-mnemon-provider-nexus|nexus-memory-bridge|project_scope\/launcher\.py/u
const HOT_RELOAD = /(^|[/@])dsh-hmr($|[/@])/u

interface Scope {workspaceId?: string}
interface AgentHeader {cwd?: string; origin?: string; parentSession?: string; delegationDepth?: number; isSeeded?: boolean}
type Ctx = any

function canonical(path: string) {
  return realpathSync(resolve(path))
}

/** Reasons the profile cannot be vouched for, empty when it can. Exported for tests. */
export function inventoryProblems(ctx: Ctx, self: unknown, workspace: string): string[] {
  const problems: string[] = []
  let cwd: string | undefined
  try { cwd = canonical(process.cwd()) } catch { /* the working directory is gone */ }
  if (cwd !== workspace) problems.push('process is not running in the workspace')
  if (ctx.get('workspaceRegistry', false) !== undefined) problems.push('a multi-workspace registry is mounted')
  const loader = ctx.get('loader', false)
  if (loader === undefined) return [...problems, 'no loader inventory']
  for (const entry of loader.entries()) {
    // A container (include or group) carries its children's config; they are listed on their own.
    if (entry === self || entry.disabled || entry.subtree) continue
    const {name: module = '', config} = entry.options ?? {}
    if (HOT_RELOAD.test(module)) problems.push('hot reload is enabled')
    let text: string
    try { text = module + ' ' + JSON.stringify(config ?? null) } catch { text = module + ' <unserializable>' }
    if (NEXUS.test(text)) problems.push(`another entry exposes Nexus: ${entry.options?.id ?? '?'}`)
  }
  return problems
}

/** A digest of the whole loader tree: every entry's id, module, state and own config. */
export function inventoryDigest(ctx: Ctx): string {
  const rows: string[] = []
  for (const entry of ctx.get('loader', false)?.entries() ?? []) {
    let config: string
    try { config = entry.subtree ? '<container>' : JSON.stringify(entry.options?.config ?? null) } catch { config = '<unserializable>' }
    rows.push(JSON.stringify([entry.id ?? entry.options?.id, entry.options?.name, !!entry.disabled, config]))
  }
  return createHash('sha256').update(rows.sort().join('\n')).digest('hex')
}

export async function apply(ctx: Ctx, config: HostConfig) {
  if (!config || typeof config.instanceId !== 'string' || !config.instanceId.trim()) throw Error('instanceId is required')
  if (typeof config.workspace !== 'string' || !config.workspace.startsWith('/')) throw Error('workspace must be absolute')
  if (!config.binding || typeof config.binding.journalDir !== 'string' || !config.binding.journalDir.startsWith('/'))
    throw Error('a private absolute journalDir is required')
  if (!config.readBinding || typeof config.readBinding.executable !== 'string') throw Error('readBinding is required')
  const log = ctx.logger?.('nexus-writer-host') ?? console
  // A missing workspace is not a configuration error: DSH must still start, without Nexus.
  let workspace: string
  try { workspace = canonical(config.workspace) } catch {
    log.warn?.('Nexus is off: the configured workspace does not exist')
    return
  }
  const loader = ctx.get('loader', false)
  const self = loader?.locate?.(ctx.fiber) ? [...loader.entries()].find((e: any) => e.fiber === ctx.fiber) : undefined
  // At start the loader may not have mounted every entry yet. Saves therefore re-check the
  // complete inventory; the first save fixes its digest, and any later difference (an entry
  // added, removed, enabled or reconfigured) refuses saves until DSH restarts.
  const initial = inventoryProblems(ctx, self, workspace)
  if (initial.length) log.warn?.(`Nexus saves are off: ${initial.join('; ')}`)
  let pinned = initial.length === 0, baseline: string | undefined
  const stillPinned = () => {
    if (!pinned) return false
    const problems = inventoryProblems(ctx, self, workspace), digest = inventoryDigest(ctx)
    baseline ??= problems.length ? undefined : digest
    if (problems.length || digest !== baseline) {
      pinned = false
      log.warn?.(`Nexus saves revoked: ${problems.join('; ') || 'the profile changed'}`)
    }
    return pinned
  }

  // mnemon resolves a session's cwd without following links; compare canonical paths.
  const sameWorkspace = (path: unknown) => {
    if (typeof path !== 'string' || !path.startsWith('/')) return false
    try { return canonical(path) === workspace } catch { return false }
  }
  /** The executing agent is the root session of this workspace: no subagent origin, no parent or
   * fork lineage, no delegation depth. Read from the agent a tool call carries, never a request. */
  const isRoot = (agent: any) => {
    const header: AgentHeader | undefined = agent?.session?.header
    if (!header || header.origin !== undefined || header.parentSession !== undefined || header.isSeeded) return false
    if ((header.delegationDepth ?? 0) !== 0) return false
    return sameWorkspace(header.cwd)
  }
  const refuse = (what: string) => { throw Error(`nexus writer host: ${what}`) }
  const guardScope = (scope: Scope | undefined) => { if (!sameWorkspace(scope?.workspaceId)) refuse('request for another workspace') }

  const guardSource = (definition: any) => ({
    ...definition,
    create(context: any) {
      const runtime = definition.create(context)
      // A profile has no UI step that turns the Nexus provider on, and this host owns the only
      // binding it has. So the first request for this workspace enables it (idempotently).
      let enabled: Promise<void> | undefined
      const enable = (scope: Scope) => enabled ??= (async () => {
        if (!runtime.manage) return
        const call = async (mode: string, operation: string, input: unknown) => (await runtime.manage({
          sourceInstanceKey: context.sourceInstanceKey, scope, mode, operation, input, confirmed: true})).value
        const services = await call('read', 'provider-services', null)
        const nexus = services?.items?.find((s: any) => s.providerId === 'nexus')
        if (nexus?.enabled !== true || nexus?.settings?.binding !== 'project')
          await call('mutate', 'provider-service-update', {providerId: 'nexus', enabled: true, settings: {binding: 'project'}})
      })().catch(error => { enabled = undefined; log.warn?.(`Nexus provider not enabled: ${error?.message ?? error}`) })
      const wrapped: any = {
        async facts(request: any, signal?: AbortSignal) { guardScope(request?.scope); await enable(request.scope); return runtime.facts(request, signal) },
        async project(request: any, signal?: AbortSignal) { guardScope(request?.scope); await enable(request.scope); return runtime.project(request, signal) },
        mutate() { refuse(`writes go through ${TOOL} from the root session`) },
      }
      if (runtime.manage) wrapped.manage = (request: any) => { guardScope(request?.scope); return runtime.manage(request) }
      if (runtime.query) wrapped.query = (request: any) => { guardScope(request?.view?.scope); return runtime.query(request) }
      if (runtime.dispose) wrapped.dispose = () => runtime.dispose()
      return wrapped
    },
  })

  // Memory Spaces registers its Source through ctx.mnemonMemory. Below this isolated scope that
  // service is a pass-through that wraps only this host's Sources; the real service is untouched.
  const real = ctx.mnemonMemory
  const inner = ctx.isolate('mnemonMemory')
  inner.provide('mnemonMemory', Object.freeze({
    installContributions(contribution: any, options: any) {
      return real.installContributions({...contribution, sources: contribution.sources?.map(guardSource)}, options)
    },
  }))
  await installMemorySpaces(inner, [{
    instanceId: 'nexus', module: readOnlyProvider as any, config: {bindings: {project: config.readBinding}},
  }], {instanceId: config.instanceId, config: config.memorySpaces as any})

  const writer = new RootWriter(config.binding)
  ctx.effect(() => () => { void writer.dispose() }, 'nexus-writer-host: settle saves')
  const text = (value: unknown) => [{type: 'text', text: JSON.stringify(value)}]
  ctx.effect(() => ctx.tools.register({
    name: TOOL,
    description: 'Save one durable project decision or fact to this repository\'s Nexus memory, exactly as given. ' +
      'Only the root session can save, and only when the owner asked for it; the repo must be in maintain mode. ' +
      'Returns the Nexus memory_id and revision_id.',
    parameters: {
      type: 'object', additionalProperties: false, required: ['content'],
      properties: {
        content: {type: 'string', description: 'The exact text to save.'},
        category: {type: 'string', enum: ['preference', 'decision', 'fact', 'insight', 'context', 'general']},
        importance: {type: 'integer', minimum: 1, maximum: 5},
        tags: {type: 'array', items: {type: 'string'}, maxItems: 20},
      },
    },
    output: {schema: {type: 'object', additionalProperties: true}, render: (_args: unknown, value: unknown) => text(value)},
    async execute(args: unknown, exec: any) {
      if (!isRoot(exec?.agent)) refuse('only the root session of this workspace may save')
      if (!stillPinned()) refuse('the profile changed after start; restart DSH to save')
      return await writer.remember(rememberInput(args), exec?.signal)
    },
  }), `nexus-writer-host: ${TOOL}`)
}
