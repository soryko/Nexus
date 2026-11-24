// Safe DOM helpers and Logo C SVG constructor. Never passes untrusted data to innerHTML.

export const PASSTHROUGH_SCHEMA = {
  "~standard": {
    version: 1,
    vendor: "nexus-memory",
    validate: (value) => ({ value }),
  },
};

export function el(tag, opts = {}, ...children) {
  const node = document.createElement(tag);
  if (opts.className) node.className = opts.className;
  if (opts.text !== undefined && opts.text !== null) node.textContent = String(opts.text);
  if (opts.title) node.title = opts.title;
  if (opts.type) node.type = opts.type;
  if (opts.disabled !== undefined) node.disabled = Boolean(opts.disabled);
  if (opts.attrs) {
    for (const [key, val] of Object.entries(opts.attrs)) {
      if (val !== undefined && val !== null) node.setAttribute(key, String(val));
    }
  }
  if (opts.onClick) node.addEventListener("click", opts.onClick);
  for (const child of children) {
    if (child !== undefined && child !== null && child !== false) {
      node.append(child);
    }
  }
  return node;
}

export function badge(text, tone = "") {
  return el("span", { className: tone ? `nx-badge ${tone}` : "nx-badge", text });
}

export function logoSvg() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 64 64");
  svg.setAttribute("fill", "none");
  svg.setAttribute("width", "20");
  svg.setAttribute("height", "20");
  svg.setAttribute("aria-hidden", "true");

  const specs = [
    ["rect", { x: "8", y: "8", width: "48", height: "48", rx: "11", fill: "#1E242B", stroke: "#475569", "stroke-width": "2" }],
    ["path", { d: "M22 20v24M22 32h14c4 0 6-3 6-7v-1", stroke: "#94A3B8", "stroke-width": "3", "stroke-linecap": "round" }],
    ["circle", { cx: "22", cy: "20", r: "5", fill: "#F1F5F9" }],
    ["circle", { cx: "22", cy: "44", r: "5", fill: "#64748B" }],
    ["circle", { cx: "42", cy: "22", r: "6", fill: "#EDB65C" }],
    ["circle", { cx: "42", cy: "44", r: "4", stroke: "#EDB65C", "stroke-width": "2.5" }],
    ["path", { d: "M27 44h11", stroke: "#EDB65C", "stroke-width": "2.5", "stroke-dasharray": "3 3", "stroke-linecap": "round" }],
  ];
  for (const [tag, attrs] of specs) {
    const child = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) child.setAttribute(k, v);
    svg.append(child);
  }
  return svg;
}

export function cardHeader(titleText, subtitleText, badgeNodes = []) {
  return el(
    "div",
    { className: "nx-head" },
    el(
      "div",
      { className: "nx-brand" },
      el("span", { className: "nx-logo" }, logoSvg()),
      el(
        "div",
        {},
        el("div", { className: "nx-title", text: titleText }),
        subtitleText ? el("div", { className: "nx-subtitle", text: subtitleText }) : null,
      ),
    ),
    el("div", { className: "nx-badges" }, ...badgeNodes),
  );
}

export function copyButton(textToCopy, label = "Copy") {
  const btn = el("button", { className: "nx-btn", type: "button", text: label });
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(String(textToCopy));
      btn.textContent = "Copied";
      setTimeout(() => {
        btn.textContent = label;
      }, 1400);
    } catch {
      btn.textContent = "Copy manually";
      setTimeout(() => {
        btn.textContent = label;
      }, 1600);
    }
  });
  return btn;
}

export function codeRow(commandText) {
  return el(
    "div",
    { className: "nx-code-row" },
    el("code", { className: "nx-code", text: commandText, title: commandText }),
    copyButton(commandText, "Copy CLI"),
  );
}

export function outcomeTone(outcome) {
  if (outcome === "Matches at HEAD") return "ok";
  if (outcome === "Changed at HEAD") return "warn";
  if (outcome === "Missing at HEAD") return "danger";
  return "";
}

export function shortOid(oid) {
  return typeof oid === "string" && oid.length > 10 ? oid.slice(0, 10) : (oid || "none");
}

export function shortDate(iso) {
  return typeof iso === "string" ? iso.replace("T", " ").slice(0, 19) : "";
}
