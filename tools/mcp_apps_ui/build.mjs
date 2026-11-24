import { build } from "esbuild";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(__dirname, "../../src/nexus_memory/transport/mcp_apps_static");

mkdirSync(OUT_DIR, { recursive: true });
const css = readFileSync(join(__dirname, "src/shared.css"), "utf-8");

async function bundleApp(entryName, title) {
  const result = await build({
    entryPoints: [join(__dirname, `src/${entryName}.js`)],
    bundle: true,
    minify: true,
    write: false,
    format: "iife",
    platform: "browser",
    target: ["es2022"],
  });
  const js = result.outputFiles[0].text;
  // Escape closing script sequences in bundled code so inline <script> cannot terminate early.
  const safeJs = js.replace(/<\/script/gi, "<\\/script");
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${title}</title>
<style>${css}</style>
</head>
<body>
<main id="app" class="nx-shell"></main>
<script>${safeJs}</script>
</body>
</html>
`;
  const outPath = join(OUT_DIR, `${entryName}.html`);
  writeFileSync(outPath, html, "utf-8");
  return { outPath, bytes: Buffer.byteLength(html, "utf-8") };
}

const card = await bundleApp("card", "Nexus Memory Card");
const atlas = await bundleApp("atlas", "Nexus Memory Atlas");
console.log(JSON.stringify({ card, atlas }, null, 2));
