import { App, applyDocumentTheme, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import { badge, codeRow, copyButton, el, logoSvg, outcomeTone, shortDate, shortOid } from "./dom.js";

const root = document.getElementById("app");
const app = new App({ name: "Nexus Atlas", version: "0.2.0" });

const KINDS = ["all", "decision", "constraint", "procedure", "failure", "observation"];

const state = {
  overview: null,
  hits: [],
  cursor: null,
  query: "",
  kind: "all",
  selectedId: null,
  // Bumped on every selection; get/history replies from an older selection are discarded so the
  // highlighted entry, the displayed record and "Attach to Next Prompt" always agree.
  selectionGen: 0,
  // Bumped by every new search; a "Load more" page from an older search is discarded.
  searchGen: 0,
  loadingMore: false,
  // The search the cursor belongs to, as actually sent: "Load more" must repeat it exactly (the
  // store rejects a cursor whose query or kinds differ), never rebuild it from draft controls.
  applied: { query: null, kinds: [] },
  detail: null,
  history: null,
  statusText: "",
};

function applyTheme(ctx) {
  if (!ctx) return;
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
}

function firstLine(text) {
  return (String(text || "").split("\n").find((l) => l.trim()) || "(empty)").slice(0, 140);
}

function draftSearch() {
  return { query: state.query.trim() || null, kinds: state.kind !== "all" ? [state.kind] : [] };
}

function searchArgs(search, cursor = null) {
  const args = { limit: 20 };
  if (search.query !== null && search.query !== undefined) args.query = search.query;
  if (search.kinds.length > 0) args.kinds = search.kinds;
  if (cursor) args.cursor = cursor;
  return args;
}

async function runSearch() {
  const searchGen = ++state.searchGen;
  state.loadingMore = false;
  state.statusText = "Searching...";
  render();
  const search = draftSearch();
  try {
    const res = await app.callServerTool({ name: "search", arguments: searchArgs(search) });
    if (searchGen !== state.searchGen) return;
    state.applied = search;
    if (res?.isError || !res?.structuredContent) {
      state.statusText = "Search failed";
      render();
      return;
    }
    state.hits = res.structuredContent.hits || [];
    state.cursor = res.structuredContent.cursor || null;
    state.statusText = `${state.hits.length} result(s)`;
    if (state.hits.length > 0 && !state.hits.some((h) => h.memory_id === state.selectedId)) {
      await selectMemory(state.hits[0].memory_id, state.hits[0].revision_id);
      return;
    }
  } catch {
    if (searchGen !== state.searchGen) return;
    state.statusText = "Search unavailable";
  }
  render();
}

async function loadMore() {
  if (!state.cursor || state.loadingMore) return;
  const searchGen = state.searchGen;
  state.loadingMore = true;
  render();
  try {
    const res = await app.callServerTool({ name: "search", arguments: searchArgs(state.applied, state.cursor) });
    if (searchGen !== state.searchGen) return;
    if (!res?.isError && res?.structuredContent) {
      state.hits = state.hits.concat(res.structuredContent.hits || []);
      state.cursor = res.structuredContent.cursor || null;
      state.statusText = `${state.hits.length} result(s)`;
    } else {
      state.statusText = "Could not load more";
    }
  } catch {
    if (searchGen !== state.searchGen) return;
    state.statusText = "Could not load more";
  } finally {
    if (searchGen === state.searchGen) state.loadingMore = false;
  }
  render();
}

async function selectMemory(memoryId, revisionId = null) {
  const selectionGen = ++state.selectionGen;
  state.selectedId = memoryId;
  state.detail = null;
  state.history = null;
  render();
  try {
    const getArgs = revisionId ? { memory_id: memoryId, revision_id: revisionId } : { memory_id: memoryId };
    const [memRes, histRes] = await Promise.all([
      app.callServerTool({ name: "get", arguments: getArgs }),
      app.callServerTool({ name: "history", arguments: { memory_id: memoryId, limit: 20 } }),
    ]);
    if (selectionGen !== state.selectionGen) return;
    state.detail = memRes?.isError ? null : memRes?.structuredContent || null;
    state.history = histRes?.isError ? null : histRes?.structuredContent || null;
  } catch {
    if (selectionGen !== state.selectionGen) return;
    state.statusText = "Failed to inspect memory";
  }
  render();
}

function renderListPane() {
  const list = el("div", { className: "nx-atlas-list" });
  if (state.hits.length === 0) {
    list.append(el("div", { className: "nx-body nx-muted", text: "No matching memories in this bound scope." }));
    return list;
  }
  for (const hit of state.hits) {
    const selected = hit.memory_id === state.selectedId;
    const item = el(
      "button",
      {
        className: "nx-atlas-item",
        type: "button",
        attrs: { "aria-current": selected ? "true" : "false" },
        onClick: () => selectMemory(hit.memory_id),
      },
      el(
        "div",
        { className: "nx-badges" },
        badge(hit.kind, "accent"),
        el("span", { className: "nx-muted", text: shortDate(hit.created_at).slice(0, 10) }),
        hit.has_earlier_revisions ? badge("revised") : null,
      ),
      el("div", { text: firstLine(hit.excerpt) }),
      hit.tags?.length
        ? el("div", { className: "nx-tags" }, ...hit.tags.slice(0, 4).map((t) => el("span", { className: "nx-tag", text: `#${t}` })))
        : null,
    );
    list.append(item);
  }
  if (state.cursor) {
    const more = el("button", { className: "nx-btn", type: "button", text: state.loadingMore ? "Loading..." : "Load more" });
    more.disabled = state.loadingMore;
    more.addEventListener("click", loadMore);
    list.append(more);
  }
  return list;
}

function renderDetailPane() {
  const pane = el("div", { className: "nx-atlas-detail" });
  if (!state.detail) {
    pane.append(el("div", { className: "nx-muted", text: state.selectedId ? "Loading revision details..." : "Select a memory on the left to inspect its full text, revisions, and Git references." }));
    return pane;
  }
  const d = state.detail;
  const caps = app.getHostCapabilities() || {};
  const isCurrent = d.revision_id === d.current_revision_id;

  const actions = el("div", { className: "nx-actions" }, copyButton(d.memory_id, "Copy ID"));
  if (caps.updateModelContext) {
    const attachBtn = el("button", { className: "nx-btn primary", type: "button", text: "Attach to Next Prompt" });
    attachBtn.addEventListener("click", async () => {
      attachBtn.disabled = true;
      try {
        await app.updateModelContext({
          content: [{ type: "text", text: `Nexus Memory ${d.memory_id} (rev ${d.revision_id}, ${d.kind}): ${d.content}` }],
        });
        attachBtn.textContent = "Attached";
      } catch {
        attachBtn.textContent = "Attach failed";
      } finally {
        setTimeout(() => {
          attachBtn.disabled = false;
          attachBtn.textContent = "Attach to Next Prompt";
        }, 1400);
      }
    });
    actions.append(attachBtn);
  }

  pane.append(
    el(
      "div",
      { className: "nx-badges" },
      badge(d.kind, "accent"),
      badge(isCurrent ? "current" : "historical", isCurrent ? "ok" : "warn"),
      el("span", { className: "nx-mono nx-muted", text: d.memory_id }),
    ),
    el("div", { className: "nx-content-box", text: d.content || "" }),
    d.tags?.length ? el("div", { className: "nx-tags" }, ...d.tags.map((t) => el("span", { className: "nx-tag", text: `#${t}` }))) : null,
    actions,
  );

  if (Array.isArray(d.references) && d.references.length > 0) {
    const refList = el("div", { className: "nx-ref-list" });
    d.references.forEach((ref, idx) => {
      const verdictSlot = el("div", { className: "nx-muted" });
      const checkBtn = el("button", { className: "nx-btn", type: "button", text: "Verify at HEAD" });
      checkBtn.addEventListener("click", async () => {
        checkBtn.disabled = true;
        checkBtn.textContent = "Checking...";
        try {
          const res = await app.callServerTool({
            name: "compare_at_head",
            arguments: { memory_id: d.memory_id, revision_id: d.revision_id, reference_ordinal: idx },
          });
          const cmp = res?.structuredContent;
          if (!res?.isError && cmp) {
            verdictSlot.replaceChildren(badge(cmp.outcome, outcomeTone(cmp.outcome)), el("span", { text: ` ${cmp.note || ""}` }));
          } else {
            verdictSlot.textContent = "HEAD check failed.";
          }
        } finally {
          checkBtn.disabled = false;
          checkBtn.textContent = "Verify at HEAD";
        }
      });
      refList.append(
        el(
          "div",
          { className: "nx-ref-item" },
          el(
            "div",
            { className: "nx-ref-top" },
            el("span", { className: "nx-mono", text: `${ref.path} @ ${shortOid(ref.commit_oid)}` }),
            el("div", { className: "nx-actions" }, copyButton(ref.path, "Copy path"), checkBtn),
          ),
          verdictSlot,
        ),
      );
    });
    pane.append(el("div", { className: "nx-muted", text: "Git References" }), refList);
  }

  if (state.history?.entries?.length) {
    const histList = el("div", { className: "nx-ref-list" });
    for (const entry of state.history.entries) {
      histList.append(
        el(
          "div",
          { className: "nx-history-item" },
          el("span", { className: "nx-mono", text: `${entry.revision_id.slice(0, 8)} · ${entry.kind}` }),
          el(
            "div",
            { className: "nx-actions" },
            el("span", { className: "nx-muted", text: shortDate(entry.created_at) }),
            entry.is_current ? badge("HEAD", "ok") : el("button", {
              className: "nx-btn",
              type: "button",
              text: "View",
              onClick: () => selectMemory(d.memory_id, entry.revision_id),
            }),
          ),
        ),
      );
    }
    pane.append(el("div", { className: "nx-muted", text: `Revision Chain (${state.history.entries.length})` }), histList);
  }

  return pane;
}

function renderDiagnostic(data) {
  const isDrift = data.scope_state === "device_drift";
  const body = el(
    "div",
    { className: "nx-body" },
    el(
      "div",
      { className: isDrift ? "nx-banner" : "nx-banner danger" },
      el("div", { className: "nx-banner-title", text: data.scope_state || "Scope unavailable" }),
      el("div", { className: "nx-muted", text: data.message || "Project scope is unavailable." }),
    ),
  );
  if (data.recover_command) body.append(codeRow(data.recover_command));
  if (data.enroll_command) body.append(codeRow(data.enroll_command));
  root.replaceChildren(el("div", { className: "nx-shell" }, el("div", { className: "nx-card" }, body)));
}

function render() {
  if (state.overview && "scope_state" in state.overview) {
    renderDiagnostic(state.overview);
    return;
  }

  const input = el("input", {
    className: "nx-input",
    attrs: { type: "search", placeholder: "Search literal terms...", value: state.query },
  });
  input.value = state.query;
  input.addEventListener("input", () => {
    state.query = input.value;
  });

  const form = el("form", { className: "nx-search-row" }, input, el("button", { className: "nx-btn primary", type: "submit", text: "Search" }));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch();
  });

  const filterRow = el(
    "div",
    { className: "nx-filters" },
    ...KINDS.map((k) =>
      el("button", {
        className: "nx-chip",
        type: "button",
        text: k,
        attrs: { "aria-pressed": state.kind === k ? "true" : "false" },
        onClick: () => {
          state.kind = k;
          runSearch();
        },
      }),
    ),
  );

  const countText = state.overview
    ? `${state.overview.active_memories} active · ${state.overview.revisions} revisions · ${state.overview.verification}`
    : "Read-only Memory Atlas";

  const bar = el(
    "div",
    { className: "nx-atlas-bar" },
    el(
      "div",
      { className: "nx-atlas-top" },
      el(
        "div",
        { className: "nx-brand" },
        el("span", { className: "nx-logo" }, logoSvg()),
        el("div", {}, el("div", { className: "nx-title", text: "Nexus Memory Atlas" }), el("div", { className: "nx-subtitle", text: countText })),
      ),
      el("div", { className: "nx-badges" }, badge("read-only", "ok"), badge(state.statusText || "ready")),
    ),
    form,
    filterRow,
  );

  root.replaceChildren(
    el(
      "div",
      { className: "nx-atlas-root" },
      bar,
      el("div", { className: "nx-atlas-grid" }, renderListPane(), renderDetailPane()),
    ),
  );
}

// open_atlas's own arguments define the first page's search; adopt them so "Load more" repeats
// that search and the controls show the filters actually applied.
app.ontoolinput = ({ arguments: args } = {}) => {
  const query = typeof args?.query === "string" ? args.query : null;
  const kind = typeof args?.kind === "string" && KINDS.includes(args.kind) ? args.kind : null;
  state.applied = { query, kinds: kind ? [kind] : [] };
  state.query = query || "";
  state.kind = kind || "all";
};

app.ontoolresult = (result) => {
  const sc = result?.structuredContent;
  if (!sc || typeof sc !== "object") return;
  state.overview = sc;
  if (Array.isArray(sc.hits)) {
    state.hits = sc.hits;
    state.cursor = sc.cursor || null;
    state.statusText = `${state.hits.length} loaded`;
    if (state.hits.length > 0) {
      selectMemory(state.hits[0].memory_id, state.hits[0].revision_id);
      return;
    }
  }
  render();
};

app.onhostcontextchanged = (ctx) => applyTheme(ctx);
app.ontoolcancelled = ({ reason }) => {
  state.statusText = reason || "Cancelled";
  render();
};

render();
(async () => {
  try {
    await app.connect();
    applyTheme(app.getHostContext());
  } catch (err) {
    state.statusText = err instanceof Error ? err.message : "Connect failed";
    render();
  }
})();
