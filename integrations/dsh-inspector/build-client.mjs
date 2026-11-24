// Inline the same codec factory into the standalone DSH ModuleLoader entry.
import { readFile, writeFile } from 'node:fs/promises';
import { createInspectorApi } from './descriptor.js';
const target = new URL('./client.js', import.meta.url);
const client = await readFile(target, 'utf8');
const region = /(?<=    \/\/ BEGIN generated descriptor\n)[\s\S]*?(?=    \/\/ END generated descriptor\n)/;
if (!region.test(client)) throw Error('Missing generated descriptor markers');
const updated = client.replace(region, () => createInspectorApi.toString().split('\n').map(line => `    ${line}`).join('\n') + '\n');
if (process.argv.includes('--check')) {
  if (updated !== client) throw Error('Stale descriptor: run node build-client.mjs');
} else if (updated !== client) await writeFile(target, updated);
