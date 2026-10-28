// Step 3: SOP document, evidence, inline edits and the "Needs your input" panel.
import { state, emit, segmentMap, answersSig, isAnswered, openMandatory } from "./state.js";
import { el, icon, badge, fmtTime, toast, $ } from "./ui.js";

const CATEGORY = {
  approval_rule: "Approval rule", threshold: "Limit or threshold", eligibility: "Decision criteria",
  permission_access: "Access & permissions", failure_handling: "When things go wrong", verification: "How to check",
  ambiguous_term: "Vague wording", missing_document: "Missing document", conflict: "Sources disagree",
  visual_only_evidence: "Seen on screen only", invented_detail: "Detail not in sources", other: "Other",
};
const KIND_ICON = { note_line: "notes", transcript_turn: "mic", narration: "mic", screen_text: "video", reviewer_answer: "user" };
const FILTERS = [["all", "All"], ["needs_confirmation", "Needs confirmation"], ["inferred", "Inferred"], ["warnings", "Warnings"]];

let sheetOpen = false;
let handlers = {};

function thumbFor(segId) {
  for (const s of state.sources) if (s.thumbs?.[segId]) return s.thumbs[segId];
  return null;
}

function evidence(refs) {
  if (!refs.length) return el("p", { class: "small muted" }, "No source quote was given for this item.");
  const segs = segmentMap();
  return el("details", { class: "evidence" },
    el("summary", {}, `Show evidence (${refs.length})`),
    el("ul", {}, refs.map((r) => {
      const s = segs[r.segment_id] || {};
      const thumb = thumbFor(r.segment_id);
      return el("li", {}, icon(KIND_ICON[s.kind] || "notes"),
        el("div", {},
          el("div", { class: "mono muted" }, r.segment_id, s.start_s != null ? ` · ${fmtTime(s.start_s)}` : "", s.kind === "reviewer_answer" ? " · your answer" : ""),
          el("q", {}, r.quote)),
        thumb ? el("img", { src: thumb, alt: `Screen at ${fmtTime(s.start_s)}`, loading: "lazy" }) : null);
    })));
}

function flash(id) {
  const node = document.getElementById("item-" + id);
  if (!node) return;
  if (sheetOpen) toggleSheet(false);
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  node.classList.remove("flash");
  void node.offsetWidth;
  node.classList.add("flash");
}

function editor(step, host, textNode) {
  const area = el("textarea", { rows: 3, "aria-label": `Edit step ${step.number}` });
  area.value = step.action;
  const confirm = el("input", { type: "checkbox" });
  const box = el("div", { class: "edit-box" }, area,
    el("div", { class: "edit-row" },
      el("label", { class: "check-label" }, confirm, "Mark as confirmed"),
      el("div", {},
        el("button", { type: "button", class: "btn ghost sm", onclick: () => { box.replaceWith(textNode); } }, "Cancel"),
        el("button", { type: "button", class: "btn primary sm", onclick: () => {
          const v = area.value.trim();
          if (!v) { area.focus(); return; }
          const changed = v !== step.action;
          step.action = v;
          if (changed) state.edited[step.id] = true;
          step.status = confirm.checked ? "supported" : changed ? "needs_confirmation" : step.status;
          emit("review");
          toast(confirm.checked ? "Step saved and marked as confirmed" : "Step saved — it now needs staff confirmation");
        } }, "Save"))));
  textNode.replaceWith(box);
  area.focus();
}

function stepItem(step) {
  const text = el("p", { class: "step-text" }, step.action);
  const card = el("div", { class: "step-card" },
    el("div", { class: "item-row" },
      el("div", { class: "step-meta" },
        badge(step.status),
        step.kind !== "action" ? el("span", { class: "badge neutral" }, step.kind === "decision" ? "Decision" : "Check") : null,
        step.actor ? el("span", { class: "badge neutral" }, icon("user"), step.actor) : null,
        state.edited[step.id] ? el("span", { class: "badge edited" }, icon("pencil"), "Edited") : null),
      el("button", { type: "button", class: "btn ghost sm icon-only edit-btn", "aria-label": `Edit step ${step.number}`, onclick: () => editor(step, card, text) }, icon("pencil"))),
    text,
    step.kind === "decision" ? el("div", { class: "callout" }, icon("arrow"), el("span", {}, el("b", {}, "If: "), step.condition || "condition not stated — see questions")) : step.condition ? el("div", { class: "callout" }, icon("arrow"), el("span", {}, el("b", {}, "When: "), step.condition)) : null,
    step.expected_result ? el("div", { class: "callout result" }, icon("check"), el("span", {}, el("b", {}, "Expected result: "), step.expected_result)) : null,
    step.ui_elements.length ? el("div", { class: "chips" }, step.ui_elements.map((u) => el("span", { class: "chip mono" }, u))) : null,
    evidence(step.source_refs));
  return el("li", { class: "step", id: "item-" + step.id }, el("span", { class: "step-num", "aria-hidden": "true" }, step.number), el("div", { class: "sr-only" }, `Step ${step.number}`), card);
}

const show = (item) => state.filter === "all" || item.status === state.filter;

function documentView(sop) {
  const steps = sop.steps.filter(show), pre = sop.prerequisites.filter(show);
  const warnOnly = state.filter === "warnings";
  const section = (id, title, ic, body) => el("section", { id: "sec-" + id, "aria-labelledby": "h-" + id }, el("h3", { id: "h-" + id }, icon(ic), title), body);
  return el("article", { class: "card doc", "aria-label": "SOP document" },
    el("header", { id: "sec-purpose" }, el("h2", { class: "doc-title", id: "p3-title" }, sop.title), el("p", { class: "doc-purpose" }, sop.purpose)),
    !warnOnly && section("pre", "Before you start", "list", pre.length
      ? el("ul", { class: "items" }, pre.map((p) => el("li", { class: "item", id: "item-" + p.id }, el("div", { class: "item-row" }, el("span", {}, p.text), badge(p.status)), evidence(p.source_refs))))
      : el("p", { class: "muted" }, state.filter === "all" ? "No prerequisites were found in the sources." : "Nothing matches this filter.")),
    !warnOnly && section("steps", "Steps", "check", steps.length ? el("ol", { class: "steps" }, steps.map(stepItem)) : el("p", { class: "muted" }, "No steps match this filter.")),
    (state.filter === "all" || warnOnly) && section("warnings", "Warnings", "alert", sop.warnings.length
      ? el("ul", { class: "items" }, sop.warnings.map((w) => el("li", { class: "warn-callout " + (w.severity === "critical" ? "critical" : ""), id: "item-" + w.id },
        icon("alert"), el("div", {}, el("div", { class: "item-row" }, el("b", {}, w.text), el("span", { class: "step-meta" }, w.severity === "critical" ? el("span", { class: "badge critical" }, icon("alert"), "Critical") : null, badge(w.status))), evidence(w.source_refs)))))
      : el("p", { class: "muted" }, "No warnings.")),
    state.filter === "all" && section("checklist", "Review checklist", "shield", el("ul", { class: "checklist" }, sop.checklist.map((c) => el("li", {}, c.text)))));
}

function outline(sop) {
  const link = (id, label, n) => el("a", { href: "#sec-" + id, onclick: (e) => { e.preventDefault(); document.getElementById("sec-" + id)?.scrollIntoView({ behavior: "smooth" }); } }, label, n != null ? el("span", { class: "count" }, n) : null);
  return el("nav", { class: "card outline", "aria-label": "SOP sections" },
    link("purpose", "Overview"), link("pre", "Before you start", sop.prerequisites.length), link("steps", "Steps", sop.steps.length),
    link("warnings", "Warnings", sop.warnings.length), link("checklist", "Checklist", sop.checklist.length));
}

function questionCard(q) {
  const draft = state.drafts[q.id] || state.answers[q.id] || { answer: "", na: false, reason: "" };
  state.drafts[q.id] = { ...draft };
  const d = state.drafts[q.id];
  const answered = isAnswered(q);
  const area = el("textarea", { rows: 2, placeholder: "Answer from staff…", "aria-label": "Answer: " + q.question, disabled: d.na });
  area.value = d.answer || "";
  const reason = el("input", { type: "text", placeholder: "Why doesn't it apply?", "aria-label": "Reason not applicable", hidden: !d.na });
  reason.value = d.reason || "";
  const na = el("input", { type: "checkbox", checked: !!d.na });
  const status = el("span", { class: "small " + (answered ? "ok-tick" : "muted") }, answered ? [icon("check"), "Saved"] : "Not answered yet");
  const markDirty = () => { status.className = "small muted"; status.textContent = "Unsaved changes"; };
  area.addEventListener("input", () => { d.answer = area.value; markDirty(); });
  reason.addEventListener("input", () => { d.reason = reason.value; markDirty(); });
  na.addEventListener("change", () => { d.na = na.checked; reason.hidden = !na.checked; area.disabled = na.checked; markDirty(); });
  const save = () => {
    if (!d.na && !(d.answer || "").trim()) { area.focus(); toast("Type an answer or tick “Not applicable” first.", { type: "error" }); return; }
    state.answers[q.id] = { question: q.question, answer: d.answer || "", na: !!d.na, reason: d.reason || "" };
    emit("review");
    toast("Answer saved");
  };
  const related = q.related_item_ids.filter((id) => document.getElementById("item-" + id) || state.sop.steps.some((s) => s.id === id));
  return el("li", { class: "qcard " + q.severity + (answered ? " answered" : ""), "data-q": q.id },
    el("div", { class: "step-meta" },
      q.severity === "mandatory" ? el("span", { class: "badge mandatory" }, icon("alert"), "Required") : el("span", { class: "badge optional" }, "Optional"),
      el("span", { class: "small muted" }, CATEGORY[q.category] || q.category)),
    el("p", { class: "q" }, q.question),
    el("p", { class: "reason" }, q.reason),
    related.length ? el("div", { class: "q-related" }, related.map((id) => {
      const step = state.sop.steps.find((s) => s.id === id);
      return el("button", { type: "button", onclick: () => flash(id) }, step ? `Go to step ${step.number}` : "Go to item");
    })) : null,
    area,
    el("label", { class: "check-label" }, na, "Not applicable"),
    reason,
    el("div", { class: "q-actions" }, status, el("button", { type: "button", class: "btn secondary sm", onclick: save }, "Save")));
}

function ring(done, total) {
  const r = 26, c = 2 * Math.PI * r, pct = total ? done / total : 1;
  const wrap = el("div", { class: "ring", role: "img", "aria-label": `${done} of ${total} answered` });
  wrap.innerHTML = `<svg viewBox="0 0 64 64" aria-hidden="true"><circle class="track" cx="32" cy="32" r="${r}"/><circle class="bar" cx="32" cy="32" r="${r}" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - pct)}"/></svg><span>${done}/${total}</span>`;
  return wrap;
}

function questionsPanel(sop) {
  const qs = sop.questions;
  const done = qs.filter(isAnswered).length;
  const mand = qs.filter((q) => q.severity === "mandatory"), opt = qs.filter((q) => q.severity !== "mandatory");
  return el("aside", { class: "card qpanel" + (sheetOpen ? " open" : ""), id: "qpanel", "aria-labelledby": "q-title" },
    el("div", { class: "qpanel-head" }, ring(done, qs.length),
      el("div", {}, el("h3", { id: "q-title" }, "Needs your input"), el("p", { class: "small muted" }, `${done} of ${qs.length} answered`)),
      el("button", { type: "button", class: "btn ghost icon-only sm sheet-close", "aria-label": "Close questions", onclick: () => toggleSheet(false) }, icon("x"))),
    qs.length ? null : el("div", { class: "empty" }, icon("check", "xl"), el("p", {}, el("b", {}, "Nothing to confirm"), el("br"), el("span", { class: "muted" }, "Every item is backed by your sources."))),
    mand.length ? [el("h4", { class: "qgroup-title" }, `Required (${mand.length})`), el("ul", {}, mand.map(questionCard))] : null,
    opt.length ? [el("h4", { class: "qgroup-title" }, `Optional (${opt.length})`), el("ul", {}, opt.map(questionCard))] : null);
}

function toggleSheet(open) {
  sheetOpen = open;
  $("#qpanel")?.classList.toggle("open", open);
  $("#fab")?.setAttribute("aria-expanded", String(open));
  if (open) $("#qpanel textarea")?.focus();
}

export function renderReview(root, h) {
  handlers = h || handlers;
  const sop = state.sop;
  root.replaceChildren();
  if (!sop) return;
  const open = openMandatory().length;
  const counts = {
    all: null,
    needs_confirmation: [...sop.steps, ...sop.prerequisites].filter((i) => i.status === "needs_confirmation").length,
    inferred: [...sop.steps, ...sop.prerequisites].filter((i) => i.status === "inferred").length,
    warnings: sop.warnings.length,
  };
  const changed = answersSig() !== state.genSig;
  root.append(
    el("header", { class: "panel-head" }, el("p", { class: "eyebrow" }, icon("sparkles"), `Draft v${state.version}`, state.report?.model ? ` · ${state.report.model}` : ""),
      el("p", { class: "muted" }, "Click any step to see the source behind it. Answer the questions, then regenerate.")),
    el("div", { class: "rv-bar" },
      el("div", { class: "rv-filters", role: "group", "aria-label": "Filter items" }, FILTERS.map(([key, label]) =>
        el("button", { type: "button", class: "chip filter", "aria-pressed": String(state.filter === key), onclick: () => { state.filter = key; emit("review"); } },
          label, counts[key] != null ? el("span", { class: "count" }, counts[key]) : null))),
      el("div", { class: "rv-tools" },
        el("span", { class: "badge " + (open ? "mandatory" : "supported") }, icon(open ? "alert" : "check"), open ? `${open} required open` : "All required answered"),
        el("button", { type: "button", class: "btn secondary", id: "regen", disabled: !changed, title: changed ? "" : "Answer or change a question first", onclick: (e) => handlers.regenerate?.(e.currentTarget) }, icon("refresh"), "Regenerate with answers"),
        el("button", { type: "button", class: "btn primary", onclick: () => handlers.go?.(4) }, "Continue to export", icon("arrow")))),
    el("div", { class: "rv-grid" }, outline(sop), documentView(sop), questionsPanel(sop)),
    el("button", { type: "button", class: "btn primary fab", id: "fab", "aria-controls": "qpanel", "aria-expanded": String(sheetOpen), onclick: () => toggleSheet(!sheetOpen) },
      icon("help"), sop.questions.length - sop.questions.filter(isAnswered).length ? `${sop.questions.length - sop.questions.filter(isAnswered).length} to confirm` : "Questions"));
}
