// Small DOM + UI helpers shared by all modules.
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k in node && typeof v !== "string") node[k] = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const NS = "http://www.w3.org/2000/svg";
export function icon(name, cls = "") {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "ic " + cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(NS, "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

export const STATUS = {
  supported: { icon: "check", label: "Backed by source" },
  inferred: { icon: "eye", label: "Seen on screen only" },
  needs_confirmation: { icon: "help", label: "Needs staff confirmation" },
};
export const badge = (status) => el("span", { class: "badge " + status }, icon(STATUS[status].icon), STATUS[status].label);

export function fmtTime(s) {
  if (s == null) return "";
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(sec).padStart(2, "0");
}
export function fmtSize(b) {
  if (!b) return "";
  return b < 1e6 ? Math.max(1, Math.round(b / 1e3)) + " KB" : (b / 1e6).toFixed(1) + " MB";
}

export function toast(message, { type = "", action, onAction, timeout = 5000 } = {}) {
  const box = $("#toasts");
  const t = el("div", { class: "toast " + type },
    icon(type === "error" ? "alert" : "check"), el("span", {}, message),
    action ? el("button", { type: "button", onclick: () => { onAction?.(); close(); } }, action) : null);
  box.append(t);
  const close = () => { t.classList.add("hide"); setTimeout(() => t.remove(), 300); };
  setTimeout(close, timeout);
  return close;
}

export function openDialog(id) { const d = document.getElementById(id); if (!d.open) d.showModal(); return d; }

export function confirmDialog(title, text, okLabel = "Confirm") {
  const d = openDialog("dlg-confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  $("#confirm-ok").textContent = okLabel;
  d.returnValue = "";
  return new Promise((resolve) => d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true }));
}

export function transition(fn) {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (document.startViewTransition && !reduce) document.startViewTransition(fn); else fn();
}

export async function busy(button, fn) {
  button.classList.add("loading");
  button.setAttribute("aria-busy", "true");
  try { return await fn(); } finally { button.classList.remove("loading"); button.removeAttribute("aria-busy"); }
}

export function download(blob, name) {
  const a = el("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
}

export const slug = (t) => (t || "SOP").replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 50) || "SOP";
