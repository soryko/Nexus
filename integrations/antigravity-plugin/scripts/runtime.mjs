#!/usr/bin/env node
/**
 * Portable runtime bootstrap & floor verifier for the Nexus Memory Antigravity plugin.
 *
 * Enforces BOTH independent runtime floors:
 *   - Python >= 3.12
 *   - linked SQLite >= 3.51.3
 * plus required plugin modules (activity, atlas.studio_worker, transport.mcp_apps).
 * Uses Node.js standard library modules only so it runs directly on agy-node.
 */

import { execFileSync, spawnSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const MIN_PYTHON = [3, 12, 0];
export const MIN_SQLITE = [3, 51, 3];
export const TARGET_VERSION = '0.2.0';

const __dirname = dirname(fileURLToPath(import.meta.url));
export const DEFAULT_PLUGIN_ROOT = resolve(__dirname, '..');

const REQUIRED_IMPORTS = [
  'nexus_memory',
  'nexus_memory.activity',
  'nexus_memory.atlas.studio_worker',
  'nexus_memory.transport.mcp_apps',
];

const PROBE_CODE = [
  'import importlib.metadata, importlib.util, json, sqlite3, sys',
  `req = ${JSON.stringify(REQUIRED_IMPORTS)}`,
  'def _has_spec(name):',
  '    try:',
  '        return importlib.util.find_spec(name) is not None',
  '    except ModuleNotFoundError:',
  '        return False',
  'mods = {m: _has_spec(m) for m in req}',
  'ver = None',
  'try:',
  '    ver = importlib.metadata.version("nexus-memory")',
  'except Exception:',
  '    ver = None',
  'print(json.dumps({',
  '    "executable": sys.executable,',
  '    "python": list(sys.version_info[:3]),',
  '    "sqlite": sqlite3.sqlite_version,',
  '    "modules": mods,',
  '    "package_version": ver,',
  '}))',
].join('\n');

export function compareVersionTuples(a, b) {
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i += 1) {
    const x = Number(a[i] ?? 0);
    const y = Number(b[i] ?? 0);
    if (x > y) return 1;
    if (x < y) return -1;
  }
  return 0;
}

export function parseVersionTuple(versionStr) {
  if (typeof versionStr !== 'string') return [0, 0, 0];
  return versionStr
    .split('.')
    .slice(0, 3)
    .map((part) => {
      const m = /^\d+/.exec(part.trim());
      return m ? Number.parseInt(m[0], 10) : 0;
    });
}

export function venvPythonPath(venvDir) {
  return process.platform === 'win32'
    ? join(venvDir, 'Scripts', 'python.exe')
    : join(venvDir, 'bin', 'python');
}

export function probePython(pythonPath, { requirePackage = true, timeoutMs = 5000 } = {}) {
  if (!pythonPath || typeof pythonPath !== 'string') {
    return { ok: false, executable: pythonPath || '', reason: 'empty interpreter path' };
  }
  const res = spawnSync(pythonPath, ['-I', '-B', '-c', PROBE_CODE], {
    encoding: 'utf-8',
    timeout: timeoutMs,
    windowsHide: true,
    env: {
      PATH: process.env.PATH || '',
      HOME: process.env.HOME || homedir(),
      SYSTEMROOT: process.env.SYSTEMROOT || '',
    },
  });
  if (res.error || res.status !== 0) {
    return {
      ok: false,
      executable: pythonPath,
      reason: res.error?.message || (res.stderr || '').trim().split('\n').pop() || `exit ${res.status}`,
    };
  }
  const line = (res.stdout || '').trim().split('\n').pop() || '';
  let info;
  try {
    info = JSON.parse(line);
  } catch {
    return { ok: false, executable: pythonPath, reason: 'unparseable probe output' };
  }
  const pyTuple = Array.isArray(info.python) ? info.python.map(Number) : [0, 0, 0];
  const sqTuple = parseVersionTuple(info.sqlite);
  const pyOk = compareVersionTuples(pyTuple, MIN_PYTHON) >= 0;
  const sqOk = compareVersionTuples(sqTuple, MIN_SQLITE) >= 0;
  const mods = info.modules && typeof info.modules === 'object' ? info.modules : {};
  const missingModules = REQUIRED_IMPORTS.filter((name) => !mods[name]);
  const reasons = [];
  if (!pyOk) reasons.push(`python ${pyTuple.join('.')} < ${MIN_PYTHON.join('.')}`);
  if (!sqOk) reasons.push(`sqlite ${info.sqlite} < ${MIN_SQLITE.join('.')}`);
  if (requirePackage && missingModules.length > 0) {
    reasons.push(`missing modules: ${missingModules.join(', ')}`);
  }
  return {
    ok: reasons.length === 0,
    floorsOk: pyOk && sqOk,
    hasPackage: missingModules.length === 0,
    executable: info.executable || pythonPath,
    python: pyTuple.join('.'),
    sqlite: info.sqlite,
    packageVersion: info.package_version || null,
    missingModules,
    reason: reasons.length ? reasons.join('; ') : 'meets both floors',
  };
}

function findOnPath(cmd) {
  const pathEnv = process.env.PATH || '';
  const exts = process.platform === 'win32' ? ['.exe', '.cmd', '.bat', ''] : [''];
  for (const dir of pathEnv.split(process.platform === 'win32' ? ';' : ':')) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = join(dir, cmd + ext);
      if (existsSync(candidate)) return candidate;
    }
  }
  return null;
}

function listOwnerVenvPythons(env) {
  const venvsDir = env.NEXUS_VENVS_DIR || join(env.HOME || homedir(), '.local', 'share', 'nexus-memory', 'venvs');
  if (!existsSync(venvsDir)) return [];
  let entries;
  try {
    entries = readdirSync(venvsDir, { withFileTypes: true });
  } catch {
    return [];
  }
  const dirs = entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => {
      const fullDir = join(venvsDir, entry.name);
      const py = venvPythonPath(fullDir);
      let mtime = 0;
      try {
        mtime = statSync(fullDir).mtimeMs;
      } catch {
        mtime = 0;
      }
      return { name: entry.name, python: py, mtime };
    })
    .filter((item) => existsSync(item.python));

  dirs.sort((a, b) => {
    if (a.name === TARGET_VERSION && b.name !== TARGET_VERSION) return -1;
    if (b.name === TARGET_VERSION && a.name !== TARGET_VERSION) return 1;
    return b.mtime - a.mtime || b.name.localeCompare(a.name);
  });
  return dirs.map((item) => item.python);
}

function candidateBasePythons(env) {
  const raw = [];
  if (process.platform === 'darwin') {
    raw.push(
      '/opt/homebrew/bin/python3.14',
      '/opt/homebrew/bin/python3.13',
      '/opt/homebrew/bin/python3.12',
      '/usr/local/bin/python3.14',
      '/usr/local/bin/python3.13',
      '/usr/local/bin/python3.12',
    );
  }
  for (const name of ['python3.14', 'python3.13', 'python3.12', 'python3']) {
    const hit = findOnPath(name);
    if (hit) raw.push(hit);
  }
  if (env.HOME) {
    for (const name of ['python3.14', 'python3.13', 'python3.12']) {
      raw.push(join(env.HOME, '.local', 'bin', name));
    }
  }
  const seen = new Set();
  const out = [];
  for (const item of raw) {
    if (!seen.has(item) && existsSync(item)) {
      seen.add(item);
      out.push(item);
    }
  }
  return out;
}

function readStamp(pluginData) {
  if (!pluginData) return null;
  const stampPath = join(pluginData, 'runtime-cache.json');
  if (!existsSync(stampPath)) return null;
  try {
    return JSON.parse(readFileSync(stampPath, 'utf-8'));
  } catch {
    return null;
  }
}

function writeStamp(pluginData, info) {
  if (!pluginData || !info?.ok) return;
  try {
    mkdirSync(pluginData, { recursive: true, mode: 0o700 });
    const pyStat = statSync(info.executable);
    const stampPath = join(pluginData, 'runtime-cache.json');
    writeFileSync(
      stampPath,
      JSON.stringify(
        {
          executable: info.executable,
          mtimeMs: pyStat.mtimeMs,
          python: info.python,
          sqlite: info.sqlite,
          packageVersion: info.packageVersion,
          checkedAt: new Date().toISOString(),
        },
        null,
        2,
      ) + '\n',
      { mode: 0o600 },
    );
  } catch {
    // Non-fatal cache write failure
  }
}

function tryReadCachedStamp(pluginData) {
  const stamp = readStamp(pluginData);
  if (!stamp || typeof stamp.executable !== 'string' || !existsSync(stamp.executable)) {
    return null;
  }
  try {
    const pyStat = statSync(stamp.executable);
    if (pyStat.mtimeMs !== stamp.mtimeMs) {
      return null;
    }
  } catch {
    return null;
  }
  const probed = probePython(stamp.executable, { requirePackage: true });
  return probed.ok ? probed : null;
}

function findInstallSource(pluginRoot, env) {
  if (env.NEXUS_INSTALL_SOURCE && existsSync(env.NEXUS_INSTALL_SOURCE)) {
    return env.NEXUS_INSTALL_SOURCE;
  }
  const wheelsDir = join(pluginRoot, 'wheels');
  if (existsSync(wheelsDir)) {
    const whl = readdirSync(wheelsDir).find((f) => f.endsWith('.whl'));
    if (whl) return join(wheelsDir, whl);
  }
  const devRepo = resolve(pluginRoot, '..', '..');
  if (existsSync(join(devRepo, 'pyproject.toml')) && existsSync(join(devRepo, 'src', 'nexus_memory'))) {
    return devRepo;
  }
  return null;
}

function tryBootstrapVenv({ basePython, pluginRoot, pluginData, env }) {
  const uv = env.NEXUS_UV || findOnPath('uv') || join(env.HOME || homedir(), '.local', 'bin', 'uv');
  if (!uv || !existsSync(uv)) {
    return { attempted: false, reason: 'uv binary not found' };
  }
  const installSource = findInstallSource(pluginRoot, env);
  if (!installSource) {
    return { attempted: false, reason: 'no packaged wheel or local nexus-memory source found' };
  }
  const venvDir = join(pluginData, 'venvs', TARGET_VERSION);
  mkdirSync(dirname(venvDir), { recursive: true, mode: 0o700 });
  const venvEnv = { ...env };
  delete venvEnv.UV_PYTHON;
  try {
    execFileSync(uv, ['venv', '--allow-existing', '--python', basePython, venvDir], {
      stdio: 'pipe',
      timeout: 60000,
      env: venvEnv,
    });
    const py = venvPythonPath(venvDir);
    execFileSync(uv, ['pip', 'install', '--python', py, '--no-cache', installSource], {
      stdio: 'pipe',
      timeout: 120000,
      env: venvEnv,
    });
    const verified = probePython(py, { requirePackage: true });
    if (verified.ok) {
      return { attempted: true, ok: true, info: verified };
    }
    return { attempted: true, ok: false, reason: verified.reason };
  } catch (err) {
    return { attempted: true, ok: false, reason: err?.message || 'uv bootstrap failed' };
  }
}

export function resolveRuntime({
  autoBootstrap = true,
  pluginRoot = DEFAULT_PLUGIN_ROOT,
  pluginData = process.env.PLUGIN_DATA || '',
  env = process.env,
} = {}) {
  const probes = [];

  if (env.NEXUS_PYTHON) {
    const info = probePython(env.NEXUS_PYTHON, { requirePackage: true });
    probes.push(info);
    if (info.ok) {
      return { ok: true, python: info.executable, info, source: 'NEXUS_PYTHON', probes };
    }
    return {
      ok: false,
      source: 'NEXUS_PYTHON',
      reason: `NEXUS_PYTHON (${env.NEXUS_PYTHON}) failed verification: ${info.reason}`,
      probes,
      fixHint: 'Unset NEXUS_PYTHON or point it at a venv built with Python >= 3.12, SQLite >= 3.51.3, and nexus-memory installed.',
    };
  }

  const cached = tryReadCachedStamp(pluginData);
  if (cached) {
    return { ok: true, python: cached.executable, info: cached, source: 'cache', probes: [cached] };
  }

  const pluginVenvsDir = pluginData ? join(pluginData, 'venvs') : '';
  const pluginManagedCandidates = pluginVenvsDir && existsSync(pluginVenvsDir)
    ? readdirSync(pluginVenvsDir, { withFileTypes: true })
        .filter((d) => d.isDirectory())
        .map((d) => venvPythonPath(join(pluginVenvsDir, d.name)))
        .filter((p) => existsSync(p))
    : [];

  const ownerVenvCandidates = env.NEXUS_SKIP_OWNER_VENVS === '1' ? [] : listOwnerVenvPythons(env);

  for (const [source, candidates] of [
    ['plugin-venv', pluginManagedCandidates],
    ['owner-venv', ownerVenvCandidates],
  ]) {
    for (const candidate of candidates) {
      const info = probePython(candidate, { requirePackage: true });
      probes.push(info);
      if (info.ok) {
        writeStamp(pluginData, info);
        return { ok: true, python: info.executable, info, source, probes };
      }
    }
  }

  let bestBasePython = null;
  for (const baseCandidate of candidateBasePythons(env)) {
    const info = probePython(baseCandidate, { requirePackage: true });
    probes.push(info);
    if (info.ok) {
      writeStamp(pluginData, info);
      return { ok: true, python: info.executable, info, source: 'system-python', probes };
    }
    if (info.floorsOk && !bestBasePython) {
      bestBasePython = info.executable;
    }
  }

  if (autoBootstrap && pluginData && bestBasePython) {
    const boot = tryBootstrapVenv({ basePython: bestBasePython, pluginRoot, pluginData, env });
    if (boot.ok && boot.info) {
      writeStamp(pluginData, boot.info);
      return { ok: true, python: boot.info.executable, info: boot.info, source: 'uv-bootstrap', probes };
    }
    if (boot.attempted && boot.reason) {
      probes.push({ ok: false, executable: join(pluginData, 'venvs', TARGET_VERSION), reason: boot.reason });
    }
  }

  const fixHint = bestBasePython
    ? `Qualifying Python found at ${bestBasePython}, but nexus-memory runtime is missing. Build a non-editable venv with:\n  ${bestBasePython} tools/install.py --venv "$HOME/.local/share/nexus-memory/venvs/0.2.0"`
    : 'Install Python >= 3.12 linked against SQLite >= 3.51.3 (on macOS: `brew install python@3.14 uv`) and run `python3 tools/install.py --venv "$HOME/.local/share/nexus-memory/venvs/0.2.0"`.';

  return {
    ok: false,
    source: 'none',
    reason: bestBasePython
      ? 'No Python runtime with nexus-memory>=0.2 plugin modules found'
      : 'No Python >= 3.12 with linked SQLite >= 3.51.3 found',
    probes,
    fixHint,
  };
}
