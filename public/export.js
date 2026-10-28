// Step 4: export banner, reviewer name, download cards and live header preview.
import { state, emit, allSegments, committedAnswers, openMandatory } from "./state.js";
import { request } from "./api.js";
import { el, icon, toast, busy, download, slug } from "./ui.js";

let handlers = {};

async function exportDoc(format, button) {
  await busy(button, async () => {
    try {
      const resp = await request("/api/export", { json: { sop: state.sop, answers: committedAnswers(), reviewer_name: state.reviewer, format, segments: allSegments() } });
      const name = `SOP-${slug(state.sop.title)}-v${state.version}.${format}`;
      download(await resp.blob(), name);
      toast(`Downloaded ${name}`, { action: "Start a new SOP", onAction: () => handlers.reset?.() });
    } catch (e) {
      toast(e.message, { type: "error" });
    }
  });
}

function saveSession() {
  const copy = { ...state, sources: state.sources.map(({ ctrl, ...s }) => s) };
  const name = `SOP-${slug(state.sop?.title || state.title)}-session.json`;
  download(new Blob([JSON.stringify(copy)], { type: "application/json" }), name);
  toast(`Downloaded ${name}`);
}

function preview(open) {
  const date = new Date().toISOString().slice(0, 10);
  return el("div", { class: "mini-doc", "aria-live": "polite", "aria-label": "Document header preview" },
    el("span", { class: "small muted" }, "Preview of the document header"),
    open.length ? el("div", { class: "watermark" }, "DRAFT – NOT VERIFIED") : null,
    el("h3", {}, state.sop.title),
    el("p", { class: "small muted", id: "preview-line" }, open.length ? `${open.length} required question${open.length > 1 ? "s" : ""} listed in the appendix` : `Reviewed by ${state.reviewer.trim() || "unnamed reviewer"} on ${date}`),
    el("p", { class: "small" }, [[state.sop.steps.length, "step"], [state.sop.warnings.length, "warning"], [state.sop.checklist.length, "checklist item"]].map(([n, w]) => `${n} ${w}${n === 1 ? "" : "s"}`).join(" · ")));
}

export function renderExport(root, h) {
  handlers = h || handlers;
  root.replaceChildren();
  if (!state.sop) return;
  const open = openMandatory();
  const name = el("input", { type: "text", id: "reviewer", maxlength: 120, placeholder: "e.g. Priya Sharma", autocomplete: "name" });
  name.value = state.reviewer;
  const pv = el("div", {}, preview(open));
  name.addEventListener("input", () => { state.reviewer = name.value; pv.replaceChildren(preview(open)); emit("quiet"); });
  const card = (ic, title, desc, label, onclick) => el("div", { class: "card ex-card" },
    el("span", { class: "ex-ic" }, icon(ic)), el("div", {}, el("h3", {}, title), el("p", {}, desc)),
    el("button", { type: "button", class: "btn secondary", onclick }, icon("download"), label));
  root.append(
    el("header", { class: "panel-head" }, el("h2", { id: "p4-title" }, "Export"), el("p", { class: "muted" }, "Download the SOP, or save your session to continue later.")),
    open.length
      ? el("div", { class: "banner draft", role: "status" }, icon("alert"), el("div", {}, el("b", {}, `Draft — ${open.length} required question${open.length > 1 ? "s" : ""} open. `), "The export will be marked DRAFT – NOT VERIFIED. ", el("button", { type: "button", class: "linkish", onclick: () => handlers.go?.(3) }, "Answer them")))
      : el("div", { class: "banner ready", role: "status" }, icon("check"), el("div", {}, el("b", {}, "Ready — "), "all required questions are answered.")),
    el("div", { class: "ex-grid" },
      el("div", {},
        el("div", { class: "field" }, el("label", { for: "reviewer" }, "Reviewer name"), name, el("span", { class: "small muted" }, "Shown on the document as the person who reviewed it.")),
        pv),
      el("div", { class: "ex-cards" },
        card("word", "Word document", "A .docx for sharing, printing and sign-off.", "Download .docx", (e) => exportDoc("docx", e.currentTarget)),
        card("md", "Markdown", "Plain text for wikis, Notion or GitHub.", "Download .md", (e) => exportDoc("md", e.currentTarget)),
        card("json", "Session file", "Everything you've done, to continue later.", "Download .json", saveSession))),
    el("div", { class: "panel-foot" },
      el("button", { type: "button", class: "btn ghost", onclick: () => handlers.go?.(3) }, icon("back"), "Back to review"),
      el("button", { type: "button", class: "btn ghost", onclick: () => handlers.reset?.() }, icon("refresh"), "Start a new SOP")));
}

export { saveSession };
