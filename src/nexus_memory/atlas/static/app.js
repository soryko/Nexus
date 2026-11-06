// Memory Atlas client. All stored text goes into the page through textContent only.
import { createConnections } from "/connections.js";

const $ = (id) => document.getElementById(id);
const PAGE = 20;

const state = {
  q: "", tag: "", kinds: new Set(), repo: "", path: "", prefix: "", commit: "",
  cursors: [null], index: 0, nextCursor: null,
  group: null, searchToken: 0, openToken: 0, selected: null,   // selected: {memoryId, revisionId} or null
  related: { token: 0, how: null, cursor: null, shown: 0 },      // how: ["tag"|"term", value] of the open memory
};
const trail = [];   // earlier views, newest last; Back restores the top one

function el(tag, options = {}, ...children) {
  const node = document.createElement(tag);
  if (options.text !== undefined) node.textContent = options.text;
  for (const [k, v] of Object.entries(options.attrs || {})) node.setAttribute(k, v);
  if (options.className) node.className = options.className;
  node.append(...children);
  return node;
}

const connections = createConnections({ get: (path, params) => get(path, params), onOpen: (memoryId, revisionId) => openMemory(memoryId, revisionId).catch(sessionLost) });

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
  for (const name of CITATION_FILTERS) if (state[name]) params.push([name, state[name]]);
  const cursor = state.cursors[state.index];
  if (cursor) params.push(["cursor", cursor]);
  return params;
}

// A path, prefix or commit only means something inside one repository, so the page will not send them without it.
const CITATION_FILTERS = ["repo", "path", "prefix", "commit"];
const CITATION_LABELS = { repo: "repository", path: "path", prefix: "path prefix", commit: "commit" };

function filtersActive() { return Boolean(state.q || state.tag || state.kinds.size || CITATION_FILTERS.some((n) => state[n])); }

function renderFilters() {
  const parts = [];
  if (state.q) parts.push(`text “${state.q}”`);
  if (state.tag) parts.push(`tag “${state.tag}”`);
  if (state.kinds.size) parts.push(`kind ${[...state.kinds].join(", ")}`);
  for (const name of CITATION_FILTERS) {
    if (state[name]) parts.push(`${CITATION_LABELS[name]} “${name === "commit" ? state[name].slice(0, 12) : state[name]}”`);
  }
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

function hitItem(hit) {
  const meta = `${hit.kind}${hit.tags.length ? " · " + hit.tags.join(", ") : ""} · ${hit.created_at.slice(0, 10)}`;
  const button = el("button", { attrs: { type: "button" } },
    el("span", { text: meta, className: "meta" }),
    el("span", { text: firstLine(hit.excerpt), className: "excerpt" }));
  button.addEventListener("click", () => openMemory(hit.memory_id, hit.revision_id).catch(sessionLost));
  return el("li", {}, button);
}

function renderResults(page) {
  const list = $("results");
  list.replaceChildren();
  for (const hit of page.items) list.append(hitItem(hit));
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
  const token = ++state.searchToken;
  renderFilters();
  const { status, body } = await get("/api/search", queryParams());
  if (token !== state.searchToken) return;           // a newer request owns the page
  if (status !== 200) { $("notice").textContent = "That search was not accepted."; return; }
  if (body.restarted) {
    state.cursors = [null]; state.index = 0;
    $("notice").textContent = "The memories changed while you were paging, so the list restarted from the first page.";
  } else {
    $("notice").textContent = "";
  }
  renderResults(body);
}

function clearInspector(message) {
  for (const id of ["memory-title", "memory-flag", "memory-body", "memory-diff"]) $(id).textContent = "";
  for (const id of ["memory-meta", "memory-refs", "memory-history"]) $(id).replaceChildren();
  $("memory").hidden = true;
  $("inspect-empty").textContent = message;
  $("inspect-empty").hidden = false;
  connections.hide();
}

function currentView() {
  if (!state.selected) return null;
  return {
    search: { q: state.q, tag: state.tag, repo: state.repo, path: state.path, prefix: state.prefix, commit: state.commit, kinds: [...state.kinds], group: state.group, cursors: [...state.cursors], index: state.index },
    focus: connections.snapshot(),
    selected: { ...state.selected },
  };
}

function pushTrail() {
  const view = currentView();
  if (!view || !view.focus) return;
  trail.push(view);
  history.pushState({ depth: trail.length }, "");
  $("back").disabled = false;
}

// `viaTrail` is true when Back is replaying a saved view, so nothing is pushed on top of it.
async function openMemory(memoryId, revisionId, options = {}) {
  const token = ++state.openToken;
  if (!options.viaTrail && state.selected && (state.selected.memoryId !== memoryId || state.selected.revisionId !== revisionId)) pushTrail();
  state.selected = { memoryId, revisionId };
  const [meta, text, diff, past] = await Promise.all([
    get("/api/inspect", [["id", memoryId], ["rev", revisionId]]),
    get("/api/body", [["id", memoryId], ["rev", revisionId]]),
    get("/api/compare", [["id", memoryId], ["rev", revisionId]]),
    get("/api/history", [["id", memoryId], ["limit", "100"]]),
  ]);
  if (token !== state.openToken) return false;       // a later choice owns the inspector
  if (meta.status !== 200 || text.status !== 200) {
    state.selected = null;
    clearInspector("That memory is no longer available.");
    return false;
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
  renderReferences(m.references);
  resetRelated(m.tags);
  renderDiff(diff);
  renderHistory(memoryId, past, m.revision_id);
  if (!options.viaTrail) { $("inspector").focus(); await connections.show(memoryId, revisionId, m.title.slice(0, 40)); }
  return true;
}

function resetRelated(tags) {
  state.related = { token: state.related.token + 1, how: null, cursor: null, shown: 0 };
  $("related-list").replaceChildren();
  $("related-status").textContent = "";
  $("related-more").disabled = true;
  $("related-term").value = "";
  const select = $("related-tag");
  select.replaceChildren(...tags.map((tag) => el("option", { text: tag, attrs: { value: tag } })));
  select.disabled = tags.length === 0;
  $("related-by-tag").disabled = tags.length === 0;
}

// Pages through /api/related with the server's own cursor; `more` appends the next page to the list.
async function findRelated(how, more) {
  if (!state.selected) return;
  const related = state.related;
  const token = more ? related.token : ++related.token;
  const memoryId = state.selected.memoryId;
  const params = [["id", memoryId], ["limit", "10"], [(more ? related.how : how)[0], (more ? related.how : how)[1]]];
  if (more && related.cursor) params.push(["cursor", related.cursor]);
  const { status, body } = await get("/api/related", params);
  if (token !== related.token || !state.selected || state.selected.memoryId !== memoryId) return;
  if (status !== 200) {
    $("related-list").replaceChildren();
    $("related-more").disabled = true;
    $("related-status").textContent = "That relation was not accepted: the tag or term must be on this memory.";
    return;
  }
  const page = body.page;
  if (!more || page.restarted) { $("related-list").replaceChildren(); related.shown = 0; }
  for (const hit of page.items) $("related-list").append(hitItem(hit));
  related.how = more ? related.how : how;
  related.shown += page.items.length;
  related.cursor = page.cursor;
  $("related-more").disabled = !page.cursor;
  $("related-status").textContent = page.matched === 0
    ? `No other memory ${body.reason}.`
    : `Showing 1–${related.shown} of ${page.matched} related memories, each of which ${body.reason}.`;
}

function renderReferences(references) {
  const list = $("memory-refs");
  list.replaceChildren();
  if (!references.length) { list.append(el("li", { text: "none recorded" })); return; }
  for (const r of references) list.append(el("li", { text: `${r.path} @ ${r.commit_oid.slice(0, 8)} (${r.repository_id})` }));
}

function renderDiff(reply) {
  const box = $("memory-diff");
  if (reply.status !== 200) { box.textContent = "The comparison is not available."; return; }
  const c = reply.body;
  if (c.state === "root") box.textContent = "First revision: there is nothing to compare it with.";
  else if (c.state === "too_large") box.textContent = "This revision and its parent are too large to compare here.";
  else box.textContent = c.lines.join("\n") + (c.truncated ? "\n… (comparison truncated)" : "");
}

function renderHistory(memoryId, reply, selectedRevision) {
  const list = $("memory-history");
  list.replaceChildren();
  if (reply.status !== 200) { list.append(el("li", { text: "The revision list is not available." })); return; }
  for (const entry of reply.body.entries) {
    const label = `${entry.created_at.slice(0, 19)} · ${entry.is_current ? "current" : "older"}${entry.revision_id === selectedRevision ? " · shown" : ""}`;
    const button = el("button", { text: "Open revision", attrs: { type: "button" } });
    button.addEventListener("click", () => openMemory(memoryId, entry.revision_id));
    list.append(el("li", {}, button, ` ${label}`));
  }
}

function wire() {
  $("related-by-tag").addEventListener("click", () => {
    if ($("related-tag").value) findRelated(["tag", $("related-tag").value], false).catch(sessionLost);
  });
  $("related-by-term").addEventListener("click", () => {
    const term = $("related-term").value.trim();
    if (term) findRelated(["term", term], false).catch(sessionLost);
  });
  $("related-more").addEventListener("click", () => { findRelated(null, true).catch(sessionLost); });
  $("find").addEventListener("submit", (event) => {
    event.preventDefault();
    state.q = $("q").value.trim();
    state.tag = $("tag").value.trim();
    for (const name of CITATION_FILTERS) state[name] = $(name).value.trim();
    state.prefix = state.prefix.replace(/\/+$/, "");          // the server's prefix is a whole folder: "src", not "src/"
    $("prefix").value = state.prefix;
    if (!state.repo && (state.path || state.prefix || state.commit)) {
      $("notice").textContent = "Choose a repository to filter by path, path prefix or commit.";
      return;
    }
    runSearch(true);
  });
  $("reset").addEventListener("click", () => {
    state.q = ""; state.tag = ""; state.kinds = new Set(); state.group = null;
    $("q").value = ""; $("tag").value = "";
    for (const name of CITATION_FILTERS) { state[name] = ""; $(name).value = ""; }
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

// Replays a saved view against fresh data: lists are re-queried (an expired cursor restarts and says so),
// the memory and its connections are re-read, and a selection that is gone is dropped, never redrawn.
async function goBack() {
  const view = trail.pop();
  $("back").disabled = trail.length === 0;
  if (!view) return;
  state.q = view.search.q; state.tag = view.search.tag; state.kinds = new Set(view.search.kinds);
  state.group = view.search.group; state.cursors = view.search.cursors; state.index = view.search.index;
  for (const name of CITATION_FILTERS) { state[name] = view.search[name]; $(name).value = state[name]; }
  $("q").value = state.q; $("tag").value = state.tag;
  for (const box of document.querySelectorAll("#kinds input")) box.checked = state.kinds.has(box.value);
  await refreshOverview();
  await runSearch(false);
  const { memoryId, revisionId } = view.selected;
  state.selected = { memoryId, revisionId };
  if (await openMemory(memoryId, revisionId, { viaTrail: true })) {
    $("inspector").focus();
    await connections.restore(view.focus);
  }
}

// Explicit refresh and a page restored from the browser's back/forward cache both land here.
async function revalidate() {
  await refreshOverview();
  await runSearch(false);
  if (state.selected) {
    const { memoryId, revisionId } = state.selected;
    const view = connections.snapshot();                 // taken first: a vanished memory clears the panel
    if (await openMemory(memoryId, revisionId, { viaTrail: true })) await connections.restore(view);
  }
}

async function main() {
  await signIn();
  const overview = await refreshOverview();
  renderKinds(overview);
  wire();
  $("back").addEventListener("click", () => history.back());
  $("refresh").addEventListener("click", () => revalidate().catch(sessionLost));
  window.addEventListener("popstate", (event) => {
    // Entries pushed by this page carry {depth}. Going to a shallower entry is Back; a deeper one is the browser's
    // Forward, which cannot rebuild a view that was already popped, so step back and leave the current view alone.
    const depth = (event.state && event.state.depth) || 0;
    if (depth > trail.length) { history.back(); return; }
    if (depth < trail.length) goBack().catch(sessionLost);
  });
  window.addEventListener("pageshow", (event) => { if (event.persisted) revalidate().catch(sessionLost); });
  await runSearch(true);
}

function sessionLost() {
  state.selected = null;
  for (const id of ["results", "groups"]) $(id).replaceChildren();
  clearInspector("Not signed in or the project binding changed.");
  $("status").textContent = "Not signed in or the project binding changed. Relaunch Atlas.";
}

main().catch(sessionLost);
