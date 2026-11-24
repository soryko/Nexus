import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync, realpathSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const [checkout, ...extra] = process.argv.slice(2)
if (!checkout || extra.length) throw new Error('Usage: node run.mjs <DSH source checkout>')
const repo = realpathSync(checkout)
const tsx = join(repo, 'node_modules/tsx/dist/esm/index.mjs')
const config = join(repo, 'tsconfig.json')
if (!existsSync(tsx) || !existsSync(config)) throw new Error('DSH source checkout with existing dependencies required')
for (const scenario of ['valid', 'cancelled', 'wrong-input-id']) {
  const cwd = mkdtempSync(join(realpathSync(tmpdir()), 'dsh-capture-host-'))
  try {
    const child = spawnSync(process.execPath, ['--import', pathToFileURL(tsx).href,
      fileURLToPath(new URL('./turn.mts', import.meta.url)), scenario], {
      cwd, encoding: 'utf8', timeout: 30_000, killSignal: 'SIGKILL',
      env: {
        HOME: cwd, TMPDIR: cwd, DSH_HOME: cwd, DSH_TELEMETRY_DISABLED: '1',
        TSX_TSCONFIG_PATH: config, TSX_DISABLE_CACHE: '1',
      },
    })
    if (child.stderr) process.stderr.write(child.stderr)
    if (child.error) throw child.error
    if (child.status !== 0) throw new Error(`Host probe child (${scenario}) failed: ${child.status ?? child.signal}`)
    const entries = readdirSync(cwd)
    assert.equal(entries.length, 0, `${scenario}: disposable directory must stay empty`)
    console.log(JSON.stringify({ ...JSON.parse(child.stdout), disposableEntries: entries.length }))
  } finally {
    rmSync(cwd, { recursive: true })
  }
}
