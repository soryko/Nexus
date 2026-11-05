// Bounded graph model and SVG renderer. Every label is a text node; nothing here builds markup.
export const LIMITS = { nodes: 100, edges: 150, initial: 25 };
const SVG = "http://www.w3.org/2000/svg";
export const VIEW = { width: 720, height: 480 };

export class Graph {
  constructor(focus) {
    this.nodes = new Map();      // id -> node
    this.edges = [];             // {id, from, to, relation}
    this.pos = new Map();        // id -> {x, y}, assigned once and never changed
    this.slots = new Map();      // slot group -> next index
    this.focusId = focus.id;
    this.nodes.set(focus.id, focus);
    this.pos.set(focus.id, { x: 0, y: 0 });
  }

  get remainingNodes() { return LIMITS.nodes - this.nodes.size; }
  get remainingEdges() { return LIMITS.edges - this.edges.length; }
  get full() { return this.remainingNodes <= 0 || this.remainingEdges <= 0; }

  place(group, anchor) {
    const index = this.slots.get(group) ?? 0;
    this.slots.set(group, index + 1);
    const ring = Math.floor(index / 10), step = index % 10;
    if (anchor) {
      const base = Math.atan2(anchor.y, anchor.x);
      const angle = base + (step - 4.5) * 0.42, radius = 90 + 50 * ring;
      return { x: anchor.x + Math.cos(angle) * radius, y: anchor.y + Math.sin(angle) * radius };
    }
    const center = group === "revision-of" ? Math.PI : 0;
    const angle = center + (step - 4.5) * 0.3, radius = 160 + 80 * ring;
    return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
  }

  // Adds a neighbor of `fromId` if both budgets allow it; returns "added", "known" or "full".
  link(fromId, node, relation, group) {
    const known = this.nodes.has(node.id);
    const edgeId = `${fromId}>${relation}>${node.id}`;
    if (this.edges.some((e) => e.id === edgeId)) return "known";
    if (this.remainingEdges <= 0 || (!known && this.remainingNodes <= 0)) return "full";
    if (!known) {
      this.nodes.set(node.id, node);
      this.pos.set(node.id, this.place(group, relation === "cites-by" || group.startsWith("hub:") ? this.pos.get(fromId) : null));
    }
    this.edges.push({ id: edgeId, from: fromId, to: node.id, relation });
    return "added";
  }

  bounds() {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const p of this.pos.values()) {
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
    return { minX: minX - 60, minY: minY - 40, maxX: maxX + 60, maxY: maxY + 40 };
  }
}

export function fitCamera(graph) {
  const b = graph.bounds();
  const k = Math.min(VIEW.width / (b.maxX - b.minX), VIEW.height / (b.maxY - b.minY), 2);
  return { k, x: VIEW.width / 2 - ((b.minX + b.maxX) / 2) * k, y: VIEW.height / 2 - ((b.minY + b.maxY) / 2) * k };
}

export function centerOn(graph, id, camera) {
  const p = graph.pos.get(id) || { x: 0, y: 0 };
  return { k: camera.k, x: VIEW.width / 2 - p.x * camera.k, y: VIEW.height / 2 - p.y * camera.k };
}

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = text;
  return node;
}

// Draws the graph. `camera` is {x, y, k}; `selectedId` is highlighted; `onSelect(node)` is the click/Enter action.
export function renderGraph(container, graph, camera, selectedId, onSelect) {
  const root = svg("svg", {
    viewBox: `0 0 ${VIEW.width} ${VIEW.height}`, role: "group", "aria-label": "Relationship graph",
    class: camera.k >= 1.5 ? "close" : "",
  });
  const world = svg("g", { id: "world", transform: `translate(${camera.x} ${camera.y}) scale(${camera.k})`, "data-k": camera.k });
  for (const edge of graph.edges) {
    const a = graph.pos.get(edge.from), b = graph.pos.get(edge.to);
    world.append(svg("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: `edge ${edge.relation}`, "data-edge": edge.id }));
  }
  for (const [id, node] of graph.nodes) {
    const p = graph.pos.get(id);
    const g = svg("g", {
      class: `node ${node.type}${id === graph.focusId ? " focus" : ""}${id === selectedId ? " selected" : ""}`,
      transform: `translate(${p.x} ${p.y})`, tabindex: 0, role: "button",
      "aria-label": `${node.type}: ${node.label}`, "data-node-id": id, "data-x": p.x.toFixed(3), "data-y": p.y.toFixed(3),
    });
    g.append(svg("circle", { r: node.type === "file" ? 9 : 11 }), svg("text", { y: 24 }, node.label));
    g.addEventListener("click", (event) => { event.stopPropagation(); onSelect(node); });
    g.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(node); } });
    world.append(g);
  }
  root.append(world);
  container.replaceChildren(root);
  return root;
}
