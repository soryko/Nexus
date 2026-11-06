// The "Connections" panel: a bounded graph around one revision plus the same actions as plain lists.
import { Graph, LIMITS, VIEW, centerOn, fitCamera, renderGraph } from "./graph.js";

const $ = (id) => document.getElementById(id);
const STEP = 25;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function createConnections({ get, onOpen }) {
  let graph = null, camera = { x: VIEW.width / 2, y: VIEW.height / 2, k: 1 };
  let selectedId = null, focus = null, token = 0;
  let neighborLimit = LIMITS.initial;
  let relations = [];                    // [{kind, total, shown, omitted, items}]
  const hubs = new Map();                // file node id -> {label, matched, shown, cursor, request}

  const revisionLabel = (text) => ((text || "").split("\n").find((l) => l.trim()) || "(empty)").slice(0, 40);

  // Every redraw rebuilds the controls, which would drop keyboard focus to <body>. Controls carry a stable
  // data-key; the focused one is found again afterwards (a disabled Next falls back to its Previous).
  function draw() {
    const key = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null;
    renderGraph($("graph-canvas"), graph, camera, selectedId, select);
    drawLists();
    drawLimits();
    if (!key) return;
    const usable = (k) => { const e = document.querySelector(`[data-key="${CSS.escape(k)}"]`); return e && !e.disabled ? e : null; };
    const target = usable(key) || (key.endsWith(":next") ? usable(key.slice(0, -5) + ":prev") : null);
    if (target) target.focus();
  }

  function drawLimits() {
    const parts = [`Showing ${graph.nodes.size} of at most ${LIMITS.nodes} nodes and ${graph.edges.length} of ${LIMITS.edges} edges.`];
    if (graph.full) parts.push("The graph limit is reached; choose any item from the lists below to move the focus.");
    $("graph-limits").textContent = parts.join(" ");
  }

  function drawLists() {
    const box = $("graph-lists");
    box.replaceChildren();
    for (const rel of relations) {
      const noun = rel.kind === "cites" ? "files cited" : "other revisions";
      const section = el("section");
      section.append(el("h3", `${rel.shown} of ${rel.total} ${noun}`));
      const list = el("ul");
      for (const item of rel.items) {
        const row = el("li");
        const button = el("button", item.action);
        button.type = "button";
        button.dataset.key = `rel:${rel.kind}:${item.node.id}`;
        button.addEventListener("click", () => select(item.node));
        row.append(button, el("span", item.text));
        list.append(row);
      }
      section.append(list);
      if (rel.total > rel.shown && !graph.full) {
        const more = el("button", `Show more ${noun}`);
        more.type = "button";
        more.dataset.key = `more:${rel.kind}`;
        more.addEventListener("click", () => showMore());
        section.append(more);
      }
      box.append(section);
    }
    for (const [id, hub] of hubs) {
      const section = el("section");
      const page = hub.page;
      const start = page.items.length ? hub.index * STEP + 1 : 0, end = hub.index * STEP + page.items.length;
      section.append(el("h3", hub.filter
        ? `${page.matched} matching of ${page.total} citing memories of ${hub.label}`
        : `Showing ${start}–${end} of ${page.total} citing memories of ${hub.label}`));
      const form = el("form");
      const label = el("label", `Filter citing memories of ${hub.label}`);
      const input = el("input");
      input.dataset.key = `hub:${id}:filter`;
      input.type = "search"; input.value = hub.filter; input.maxLength = 1024;
      label.append(" ", input);
      const submit = el("button", "Filter"); submit.type = "submit"; submit.dataset.key = `hub:${id}:submit`;
      form.append(label, " ", submit);
      form.addEventListener("submit", (event) => { event.preventDefault(); hub.filter = input.value.trim(); hub.cursors = [null]; hub.index = 0; loadHub(graph.nodes.get(id)); });
      section.append(form);
      const list = el("ul");
      for (const item of hub.items) {
        const row = el("li");
        const button = el("button", "Open");
        button.type = "button";
        button.dataset.key = `hub:${id}:open:${item.node.id}`;
        button.addEventListener("click", () => select(item.node));
        row.append(button, el("span", item.text));
        list.append(row);
      }
      section.append(list);
      const prev = el("button", "Previous citing memories"); prev.type = "button"; prev.disabled = hub.index === 0; prev.dataset.key = `hub:${id}:prev`;
      prev.addEventListener("click", () => { hub.index -= 1; loadHub(graph.nodes.get(id)); });
      const next = el("button", "Next citing memories"); next.type = "button"; next.disabled = !page.cursor; next.dataset.key = `hub:${id}:next`;
      next.addEventListener("click", () => { hub.cursors = hub.cursors.slice(0, hub.index + 1); hub.cursors.push(page.cursor); hub.index += 1; loadHub(graph.nodes.get(id)); });
      section.append(prev, " ", el("span", `Page ${hub.index + 1}`), " ", next);
      box.append(section);
    }
  }

  function select(node) {
    selectedId = node.id;
    if (node.type === "file") { expandFile(node); return; }
    if (node.id !== graph.focusId) { onOpen(node.memoryId, node.revisionId); return; }
    draw();
  }

  async function loadRelations(mine) {
    const { status, body } = await get("/api/neighbors", [["id", focus.memoryId], ["rev", focus.revisionId], ["limit", String(neighborLimit)]]);
    if (mine !== token || status !== 200) return;
    relations = [];
    for (const rel of body.relations) {
      const items = [];
      for (const raw of rel.items) {
        let node, text, action;
        if (rel.kind === "cites") {
          node = { id: `f:${raw.repository_id}|${raw.path}|${raw.commit_oid}`, type: "file", label: raw.path,
                   repositoryId: raw.repository_id, path: raw.path, commit: raw.commit_oid };
          text = `${raw.path} @ ${raw.commit_oid.slice(0, 8)}`; action = "Expand";
        } else {
          node = { id: `r:${raw.revision_id}`, type: "revision", label: `revision ${raw.created_at.slice(0, 10)}`,
                   memoryId: focus.memoryId, revisionId: raw.revision_id };
          text = `revision ${raw.created_at}`; action = "Open";
        }
        graph.link(graph.focusId, node, rel.kind, rel.kind);
        items.push({ node, text, action });
      }
      relations.push({ kind: rel.kind, total: rel.total, shown: items.length, items });
    }
    draw();
  }

  async function showMore() {
    neighborLimit = Math.min(LIMITS.nodes, neighborLimit + STEP);
    await loadRelations(token);
  }

  function expandFile(node) {
    if (!hubs.has(node.id)) hubs.set(node.id, { label: node.path, filter: "", cursors: [null], index: 0, items: [], page: null, node });
    return loadHub(node);
  }

  async function loadHub(node) {
    const mine = token;
    const hub = hubs.get(node.id);
    const params = [["repo", node.repositoryId], ["path", node.path], ["commit", node.commit], ["limit", String(STEP)]];
    if (hub.filter) params.push(["q", hub.filter]);
    if (hub.cursors[hub.index]) params.push(["cursor", hub.cursors[hub.index]]);
    const { status, body } = await get("/api/citations", params);
    if (mine !== token || status !== 200) return;
    if (body.restarted) { hub.cursors = [null]; hub.index = 0; }
    hub.items = [];
    for (const hit of body.items) {
      const member = { id: `r:${hit.revision_id}`, type: "revision", label: revisionLabel(hit.excerpt),
                       memoryId: hit.memory_id, revisionId: hit.revision_id };
      if (member.id !== graph.focusId) graph.link(node.id, member, "cited-by", `hub:${node.id}`);
      hub.items.push({ node: member, text: revisionLabel(hit.excerpt) });
    }
    hub.page = body;
    draw();
  }

  function setCamera(next) { camera = next; draw(); }
  const zoom = (factor) => setCamera({ ...camera, k: Math.max(0.25, Math.min(4, camera.k * factor)) });

  function wire() {
    $("zoom-in").addEventListener("click", () => zoom(1.25));
    $("zoom-out").addEventListener("click", () => zoom(0.8));
    $("fit").addEventListener("click", () => setCamera(fitCamera(graph)));
    $("back-to-selection").addEventListener("click", () => setCamera(centerOn(graph, selectedId || graph.focusId, camera)));
    const canvas = $("graph-canvas");
    canvas.addEventListener("wheel", (event) => { event.preventDefault(); zoom(event.deltaY < 0 ? 1.1 : 0.9); }, { passive: false });
    canvas.addEventListener("keydown", (event) => {
      if (event.target !== canvas) return;
      const pan = { ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40] }[event.key];
      if (pan) { event.preventDefault(); setCamera({ ...camera, x: camera.x + pan[0], y: camera.y + pan[1] }); }
      else if (event.key === "+" || event.key === "=") zoom(1.25);
      else if (event.key === "-") zoom(0.8);
      else if (event.key === "0") setCamera(fitCamera(graph));
    });
    let drag = null;
    canvas.addEventListener("pointerdown", (event) => { if (!event.target.closest(".node")) drag = { x: event.clientX, y: event.clientY }; });
    canvas.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const rect = canvas.getBoundingClientRect(), scale = VIEW.width / rect.width;
      setCamera({ ...camera, x: camera.x + (event.clientX - drag.x) * scale, y: camera.y + (event.clientY - drag.y) * scale });
      drag = { x: event.clientX, y: event.clientY };
    });
    for (const name of ["pointerup", "pointerleave"]) canvas.addEventListener(name, () => { drag = null; });
  }
  wire();

  return {
    snapshot() {
      if (!graph) return null;
      return {
        memoryId: focus.memoryId, revisionId: focus.revisionId, label: graph.nodes.get(graph.focusId).label,
        camera: { ...camera }, selectedId, neighborLimit,
        hubs: [...hubs.values()].map((h) => ({ node: h.node, filter: h.filter, cursors: [...h.cursors], index: h.index })),
      };
    },
    hide() {
      token += 1;
      graph = null; relations = []; hubs.clear(); selectedId = null;
      $("graph-canvas").replaceChildren();
      $("graph-lists").replaceChildren();
      $("graph-limits").textContent = "";
      $("graph").hidden = true;
    },
    // Rebuilds a saved view from fresh data. Anything that is no longer visible simply is not drawn.
    async restore(snap) {
      const mine = await this.show(snap.memoryId, snap.revisionId, snap.label, snap.neighborLimit);
      for (const saved of snap.hubs) {
        hubs.set(saved.node.id, { label: saved.node.path, filter: saved.filter, cursors: saved.cursors, index: saved.index,
                                  items: [], page: null, node: saved.node });
        await loadHub(saved.node);
      }
      if (token !== mine) return;
      camera = { ...snap.camera };
      selectedId = graph.nodes.has(snap.selectedId) ? snap.selectedId : graph.focusId;
      draw();
    },
    async show(memoryId, revisionId, label, limit) {
      const mine = ++token;
      focus = { memoryId, revisionId };
      neighborLimit = limit || LIMITS.initial;
      hubs.clear();
      relations = [];
      graph = new Graph({ id: `r:${revisionId}`, type: "revision", label: label || "this revision", memoryId, revisionId });
      selectedId = graph.focusId;
      camera = { x: VIEW.width / 2, y: VIEW.height / 2, k: 1 };
      $("graph").hidden = false;
      draw();
      await loadRelations(mine);
      return mine;
    },
  };
}
