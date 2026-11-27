/** The audited DSH host for a writable Nexus child (Stage B, Phase 2).
 *
 * DSH's provider host exposes no profile inventory, so this plugin is the embedding owner the
 * provider asks for: it composes the only Memory Spaces Source that carries the Nexus child, and
 * checks the profile it runs in before and while it vouches for that Source.
 *
 * - One receiving project. The process working directory must be the configured workspace, the
 *   process must not mount a multi-workspace registry, and every request to the Source must name
 *   that workspace. A request for any other workspace is refused, not served from this grant.
 * - One Source. No other loader entry may mention the Nexus provider or bridge.
 * - Pinned. Hot reload must be off. Each write re-checks the whole loader inventory; the first
 *   write fixes its digest, and any later change refuses every write until DSH restarts.
 * - Root only, and remember only. Writes need a live root agent (no subagent origin, no parent or
 *   fork lineage); archive, link, forget and the rest are refused. The Nexus catalog enforces the
 *   repo's mode and root role again on its side, with the grant the launcher minted.
 */
import {createHash} from 'node:crypto'
import {realpathSync} from 'node:fs'
import {resolve} from 'node:path'
import {installMemorySpaces} from 'dsh-mnemon-source-memory-spaces'
import {forAuditedHost} from './index.js'
import type {Binding} from './client.js'

export const name = 'dsh-nexus-writer-host'
export const inject = ['mnemonMemory', 'loader']

export interface HostConfig {
  /** Stable Source instance id for this composed Source. */
  instanceId: string
  /** Absolute path of the one workspace (a Git checkout) this process serves. */
  workspace: string
  /** The bridge launch: the project launcher's `dsh` command, which mints the session grant. */
  binding: Binding & {journalDir: string}
  /** Memory Spaces Source settings (dataDir and the like), passed through unchanged. */
  memorySpaces?: Record<string, unknown>
}

const NEXUS = /dsh-mnemon-provider-nexus|nexus-memory-bridge|project_scope\/launcher\.py/u
const HOT_RELOAD = /(^|[/@])dsh-hmr($|[/@])/u

interface Scope {workspaceId?: string; agentId?: string}
interface AgentHeader {cwd?: string; origin?: string; parentSession?: string; delegationDepth?: number; isSeeded?: boolean}
type Ctx = any

function canonical(path: string) {
  return realpathSync(resolve(path))
}

/** Reasons the profile cannot be vouched for, empty when it can. Exported for tests. */
export function inventoryProblems(ctx: Ctx, self: unknown, workspace: string): string[] {
  const problems: string[] = []
  if (canonical(process.cwd()) !== workspace) problems.push('process is not running in the workspace')
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
  const workspace = canonical(config.workspace)
  const loader = ctx.get('loader', false)
  const self = loader?.locate?.(ctx.fiber) ? [...loader.entries()].find((e: any) => e.fiber === ctx.fiber) : undefined
  const log = ctx.logger?.('nexus-writer-host') ?? console
  // At start the loader may not have mounted every entry yet. Writes therefore re-check the
  // complete inventory; the first write fixes its digest, and any later difference (an entry
  // added, removed, enabled or reconfigured) revokes writes until DSH restarts.
  const initial = inventoryProblems(ctx, self, workspace)
  if (initial.length) log.warn?.(`Nexus stays read-only: ${initial.join('; ')}`)
  let pinned = initial.length === 0, baseline: string | undefined
  const stillPinned = () => {
    if (!pinned) return false
    const problems = inventoryProblems(ctx, self, workspace), digest = inventoryDigest(ctx)
    baseline ??= problems.length ? undefined : digest
    if (problems.length || digest !== baseline) {
      pinned = false
      log.warn?.(`Nexus writes revoked: ${problems.join('; ') || 'the profile changed'}`)
    }
    return pinned
  }

  const sourceKey = config.instanceId
  const provider = forAuditedHost(() => pinned ? {
    source: sourceKey, enabledSources: [sourceKey], receivingProjects: [workspace], exposurePinnedUntilDisposal: true,
  } : undefined)

  const agents = () => ctx.get('agents', false)
  // mnemon resolves a session's cwd without following links; compare canonical paths.
  const sameWorkspace = (scope: Scope | undefined) => {
    if (typeof scope?.workspaceId !== 'string' || !scope.workspaceId.startsWith('/')) return false
    try { return canonical(scope.workspaceId) === workspace } catch { return false }
  }
  const rootAgent = (scope: Scope | undefined) => {
    const agent = typeof scope?.agentId === 'string' ? agents()?.get(scope.agentId) : undefined
    const header: AgentHeader | undefined = agent?.session?.header
    if (!header || header.origin !== undefined || header.parentSession !== undefined || header.isSeeded) return false
    if ((header.delegationDepth ?? 0) !== 0) return false
    return typeof header.cwd === 'string' && sameWorkspace({workspaceId: header.cwd})
  }
  const refuse = (what: string) => { throw Error(`nexus writer host: ${what}`) }
  const guardScope = (scope: Scope | undefined) => { if (!sameWorkspace(scope)) refuse('request for another workspace') }

  const guardSource = (definition: any) => ({
    ...definition,
    create(context: any) {
      const runtime = definition.create(context)
      const wrapped: any = {
        facts(request: any, signal?: AbortSignal) { guardScope(request?.scope); return runtime.facts(request, signal) },
        project(request: any, signal?: AbortSignal) { guardScope(request?.scope); return runtime.project(request, signal) },
      }
      if (runtime.manage) wrapped.manage = (request: any) => { guardScope(request?.scope); return runtime.manage(request) }
      if (runtime.query) wrapped.query = (request: any) => { guardScope(request?.view?.scope); return runtime.query(request) }
      if (runtime.mutate) wrapped.mutate = (request: any) => {
        const scope = request?.view?.scope
        guardScope(scope)
        if (request?.offer?.sourceActionId !== 'remember') refuse('only remember is allowed')
        if (!rootAgent(scope)) refuse('only the root session of this workspace may write')
        if (!stillPinned()) refuse('the profile changed after start; restart DSH to write')
        return runtime.mutate(request)
      }
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
  const {journalDir, ...binding} = config.binding
  await installMemorySpaces(inner, [{
    instanceId: 'nexus', module: provider as any,
    config: {audience: 'isolated-writer', enabledSources: [sourceKey], bindings: {project: {...binding, journalDir}}},
  }], {instanceId: sourceKey, config: config.memorySpaces as any})
}
