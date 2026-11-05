// Memory Atlas client. All stored text goes into the page through textContent only.
const $ = (id) => document.getElementById(id);
const PAGE = 20;

const state = {
  q: "", tag: "", kinds: new Set(),
  cursors: [null], index: 0, nextCursor: null,
  group: null, token: 0, selected: null,
};

function el(tag, options = {}, ...children) {
  const node = document.createElement(tag);
  if (options.text !== undefined) node.textContent = options.text;
  for (const [k, v] of Object.entries(options.attrs || {})) node.setAttribute(k, v);
  if (options.className) node.className = options.className;
  node.append(...children);
  return node;
}

async function get(path, params = []) {
  const url = new URL(path, location.origin);
  for (const [k, v] of params) url.searchParams.append(k, v);
  const reply = await fetch(url);
  if (reply.status === 401 || reply.status === 503) throw new Error("session");
  return { status: reply.status, body: await reply.json() };
}

async function signIn() {
  const match = /^#k=([\w-]+)$/.exec(location.hash);
  if (!match) return;
  history.replaceState(null, "", location.pathname);   // the secret leaves the address bar at once
  const reply = await fetch("/session", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: match[1] }),
  });
  if (!reply.ok) throw new Error("sign-in refused");
}

function queryParams() {
  const params = [["limit", String(PAGE)]];
  if (state.q) params.push(["q", state.q]);
  if (state.tag) params.push(["tag", state.tag]);
  for (const kind of state.kinds) params.push(["kind", kind]);
  const cursor = state.cursors[state.index];
  if (cursor) params.push(["cursor", cursor]);
  return params;
}

function filtersActive() { return Boolean(state.q || state.tag || state.kinds.size); }

function renderFilters() {
  const parts = [];
  if (state.q) parts.push(`text “${state.q}”`);
  if (state.tag) parts.push(`tag “${state.tag}”`);
  if (state.kinds.size) parts.push(`kind ${[...state.kinds].join(", ")}`);
  $("filters").textContent = parts.length ? `Filters: ${parts.join("; ")}` : "No filters: newest memories first.";
  $("reset").hidden = !filtersActive();
}

function renderGroups(overview) {
  const list = $("groups");
  list.replaceChildren();
  const largest = Math.max(1, ...overview.counts.map(([, count]) => count));
  for (const [kind, count] of overview.counts) {
    const button = el("button", { text: `${kind}: ${count}`, attrs: { type: "button", "aria-pressed": String(state.group === kind) } });
    // Size follows count inside a capped range, so one huge group cannot hide the rest.
    button.style.setProperty("font-size", `${(1 + 0.6 * Math.min(1, count / largest)).toFixed(2)}rem`);
    button.addEventListener("click", () => {
      state.group = kind;
      state.kinds = new Set([kind]);
      for (const box of document.querySelectorAll("#kinds input")) box.checked = box.value === kind;
      runSearch(true);
      renderGroups(overview);
    });
    const item = el("li");
    item.append(button);
    list.append(item);
  }
}

function renderKinds(overview) {
  const box = $("kinds");
  for (const [kind] of overview.counts) {
    const input = el("input", { attrs: { type: "checkbox", value: kind, id: `kind-${kind}` } });
    input.addEventListener("change", () => {
      if (input.checked) state.kinds.add(kind); else state.kinds.delete(kind);
      state.group = state.kinds.size === 1 ? [...state.kinds][0] : null;
    });
    box.append(el("label", {}, input, ` ${kind}`));
  }
}

function firstLine(text) { return (text || "").split("\n").find((line) => line.trim()) || "(empty)"; }

function renderResults(page) {
  const list = $("results");
  list.replaceChildren();
  for (const hit of page.items) {
    const meta = `${hit.kind}${hit.tags.length ? " · " + hit.tags.join(", ") : ""} · ${hit.created_at.slice(0, 10)}`;
    const button = el("button", { attrs: { type: "button" } },
      el("span", { text: meta, className: "meta" }),
      el("span", { text: firstLine(hit.excerpt), className: "excerpt" }));
    button.addEventListener("click", () => openMemory(hit.memory_id, hit.revision_id));
    list.append(el("li", {}, button));
  }
  const shown = state.index * PAGE + page.items.length;
  const start = page.items.length ? state.index * PAGE + 1 : 0;
  $("count").textContent = page.matched === 0
    ? `No memories match (of ${page.total} in this project).`
    : `Showing ${start}–${shown} of ${page.matched} matching (${page.total} in this project).`;
  $("page").textContent = `Page ${state.index + 1}`;
  $("prev").disabled = state.index === 0;
  $("next").disabled = !page.cursor;
  state.nextCursor = page.cursor;
}

async function runSearch(fresh) {
  if (fresh) { state.cursors = [null]; state.index = 0; }
  const token = ++state.token;
  renderFilters();
  const { status, body } = await get("/api/search", queryParams());
  if (token !== state.token) return;                // a newer request owns the page
  if (status !== 200) { $("notice").textContent = "That search was not accepted."; return; }
  if (body.restarted) {
    state.cursors = [null]; state.index = 0;
    $("notice").textContent = "The memories changed while you were paging, so the list restarted from the first page.";
  } else {
    $("notice").textContent = "";
  }
  renderResults(body);
}

async function openMemory(memoryId, revisionId) {
  const token = ++state.token;
  state.selected = memoryId;
  const [meta, text] = await Promise.all([
    get("/api/inspect", [["id", memoryId], ["rev", revisionId]]),
    get("/api/body", [["id", memoryId], ["rev", revisionId]]),
  ]);
  if (token !== state.token) return;
  if (meta.status !== 200 || text.status !== 200) {
    $("memory").hidden = true;
    $("inspect-empty").textContent = "That memory is no longer available.";
    $("inspect-empty").hidden = false;
    return;
  }
  const m = meta.body;
  $("inspect-empty").hidden = true;
  $("memory").hidden = false;
  $("memory-title").textContent = m.title;
  $("memory-flag").textContent = m.is_historical ? "Historical revision: not the current text." : "";
  const rows = [
    ["Kind", m.kind], ["Tags", m.tags.length ? m.tags.join(", ") : "none"],
    ["Source", m.source_uri || "none"], ["Added", m.added_at], ["This revision", m.revision_created_at],
    ["Memory ID", m.memory_id], ["Revision ID", m.revision_id], ["Current revision ID", m.current_revision_id],
  ];
  const dl = $("memory-meta");
  dl.replaceChildren();
  for (const [name, value] of rows) dl.append(el("dt", { text: name }), el("dd", { text: value }));
  $("memory-body").textContent = text.body.content;
  $("inspector").focus();
}

function wire() {
  $("find").addEventListener("submit", (event) => {
    event.preventDefault();
    state.q = $("q").value.trim();
    state.tag = $("tag").value.trim();
    runSearch(true);
  });
  $("reset").addEventListener("click", () => {
    state.q = ""; state.tag = ""; state.kinds = new Set(); state.group = null;
    $("q").value = ""; $("tag").value = "";
    for (const box of document.querySelectorAll("#kinds input")) box.checked = false;
    runSearch(true);
    refreshOverview();
    $("q").focus();
  });
  $("next").addEventListener("click", () => {
    if (!state.nextCursor) return;
    state.cursors = state.cursors.slice(0, state.index + 1);
    state.cursors.push(state.nextCursor);
    state.index += 1;
    runSearch(false);
  });
  $("prev").addEventListener("click", () => {
    if (state.index === 0) return;
    state.index -= 1;
    runSearch(false);
  });
}

async function refreshOverview() {
  const { status, body } = await get("/api/overview");
  if (status !== 200) throw new Error("session");
  $("status").textContent = `${body.total} active memories`;
  renderGroups(body);
  return body;
}

async function main() {
  await signIn();
  const overview = await refreshOverview();
  renderKinds(overview);
  wire();
  await runSearch(true);
}

main().catch(() => {
  $("status").textContent = "Not signed in or the project binding changed. Relaunch Atlas.";
});
