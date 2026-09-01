// DOM helpers — the XSS defense layer (fixes C-2).
//
// RULE: user/API-derived strings NEVER pass through innerHTML interpolation.
// h() builds elements from tag + props + children; esc() is the escape hatch
// ONLY for strings we must inline into known-safe template literals (e.g.
// inside <svg> text nodes built by builders). CSP remains the second line
// of defense, never the first.

/** HTML-escape a string for safe interpolation into template literals. */
export function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * Create an element: h("div", {class: "x", onclick: fn}, children...)
 * Props: DOM properties (not attributes) where names differ (className,
 * htmlFor, dataset). Children: strings → textContent, nodes → append.
 */
export function h(tag, props = {}, ...children) {
  const isSvg = tag === "svg" || tag === "svg:*";
  const name = tag === "svg:*" ? "g" : tag;
  const el = isSvg
    ? document.createElementNS(SVG_NS, name)
    : document.createElement(name);

  for (const [key, val] of Object.entries(props || {})) {
    if (val == null || val === false) continue;
    if (key === "class") el.className = val;
    else if (key === "dataset") Object.assign(el.dataset, val);
    else if (key.startsWith("on") && typeof val === "function") {
      // onClick -> "click"; DOM event names are lowercase.
      el.addEventListener(key.slice(2).toLowerCase(), val);
    } else if (key === "text") el.textContent = val;
    else if (key === "html") {
      // Explicit opt-in for TRUSTED static markup only. Never user data.
      throw new Error("h(): 'html' prop is forbidden — build children instead (C-2)");
    } else el.setAttribute(key, val);
  }

  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** SVG element with namespace (h("svg:rect", …) etc. use plain tag names). */
export function svgEl(tag, props = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, val] of Object.entries(props || {})) {
    if (val == null || val === false) continue;
    if (key === "class") el.setAttribute("class", val);
    else el.setAttribute(key, val);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

/**
 * Replace a container's children in one move (no incremental churn).
 * Accepts nodes, arrays, null (clears).
 */
export function render(container, children) {
  while (container.firstChild) container.removeChild(container.firstChild);
  for (const child of [children].flat(Infinity)) {
    if (child == null || child === false) continue;
    container.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
}

/** $(sel) scoped to a root (default document). */
export function $(sel, root = document) {
  return root.querySelector(sel);
}
