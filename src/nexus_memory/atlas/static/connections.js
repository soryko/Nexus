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

  function draw() {
    renderGraph($("graph-canvas"), graph, camera, selectedId, select);
    drawLists();
    drawLimits();
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
        button.addEventListener("click", () => select(item.node));
        row.append(button, el("span", item.text));
        list.append(row);
      }
      section.append(list);
      if (rel.total > rel.shown && !graph.full) {
        const more = el("button", `Show more ${noun}`);
        more.type = "button";
        more.addEventListener("click", () => showMore());
        section.append(more);
      }
      box.append(section);
    }
    for (const [id, hub] of hubs) {
      const section = el("section");
      section.append(el("h3", `${hub.shown} of ${hub.matched} citing memories of ${hub.label}`));
      const list = el("ul");
      for (const item of hub.items) {
        const row = el("li");
        const button = el("button", "Open");
        button.type = "button";
        button.addEventListener("click", () => select(item.node));
        row.append(button, el("span", item.text));
        list.append(row);
      }
      section.append(list);
      if (hub.cursor && !graph.full) {
        const more = el("button", `Show more citing memories of ${hub.label}`);
        more.type = "button";
        more.addEventListener("click", () => expandFile(graph.nodes.get(id), true));
        section.append(more);
      }
      box.append(section);
    }
  }

  function select(node) {
    selectedId = node.id;
    if (node.type === "file") { expandFile(node, false); return; }
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

  async function expandFile(node, more) {
    const mine = token;
    const hub = hubs.get(node.id) || { label: node.path, matched: 0, shown: 0, cursor: null, items: [] };
    const params = [["repo", node.repositoryId], ["path", node.path], ["commit", node.commit], ["limit", String(STEP)]];
    if (more && hub.cursor) params.push(["cursor", hub.cursor]);
    const { status, body } = await get("/api/citations", params);
    if (mine !== token || status !== 200) return;
    for (const hit of body.items) {
      if (hit.revision_id === focus.revisionId) continue;
      const member = { id: `r:${hit.revision_id}`, type: "revision", label: revisionLabel(hit.excerpt),
                       memoryId: hit.memory_id, revisionId: hit.revision_id };
      const outcome = graph.link(node.id, member, "cited-by", `hub:${node.id}`);
      if (outcome !== "full") hub.items.push({ node: member, text: revisionLabel(hit.excerpt) });
    }
    hub.matched = body.matched;
    hub.shown = hub.items.length;
    hub.cursor = body.cursor;
    hubs.set(node.id, hub);
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
    async show(memoryId, revisionId, label) {
      const mine = ++token;
      focus = { memoryId, revisionId };
      neighborLimit = LIMITS.initial;
      hubs.clear();
      relations = [];
      graph = new Graph({ id: `r:${revisionId}`, type: "revision", label: label || "this revision", memoryId, revisionId });
      selectedId = graph.focusId;
      camera = { x: VIEW.width / 2, y: VIEW.height / 2, k: 1 };
      $("graph").hidden = false;
      draw();
      await loadRelations(mine);
    },
  };
}
