// Entry point: wiring, sources (step 1), generation (step 2), navigation, theme, session.
import { state, fresh, replace, emit, bus, save, loadSaved, clearSaved, readySources, allSegments, committedAnswers, answersSig } from "./state.js";
import { request } from "./api.js";
import { $, $$, el, icon, fmtTime, fmtSize, toast, openDialog, confirmDialog, transition, busy } from "./ui.js";
import { checkMedia, decodeTo16kMono, encodeWav, audioChunks, extractFrames } from "./media.js";
import { renderReview } from "./review.js";
import { renderExport, saveSession } from "./export.js";

const TRANSCRIPT_EXT = /\.(vtt|srt|txt)$/i;
const TYPE = {
  notes: { icon: "notes", label: "Notes" }, transcript: { icon: "mic", label: "Interview transcript" },
  interview: { icon: "mic", label: "Interview recording" }, screen: { icon: "video", label: "Screen recording" },
};
const handlers = { go: (n) => goStep(n), regenerate: (b) => regenerate(b), reset: () => startOver() };

// ---------- navigation ----------
const canReach = (n) => n === 1 || (n === 2 && readySources().length > 0) || (n >= 3 && !!state.sop);

function goStep(n, { focus = true } = {}) {
  if (!canReach(n)) return;
  transition(() => {
    state.started = true;
    state.step = n;
    renderChrome();
    window.scrollTo({ top: 0 });
  });
  save();
  if (focus) setTimeout(() => $(`[data-panel="${n}"] h2`)?.focus({ preventScroll: true }), 50);
}

function renderChrome() {
  $("#hero").hidden = state.started;
  $("#app").hidden = !state.started;
  $("#stepper").hidden = !state.started;
  for (const btn of $$("#stepper button")) {
    const n = +btn.dataset.step, li = btn.parentElement;
    btn.disabled = !canReach(n);
    li.classList.toggle("current", n === state.step);
    li.classList.toggle("done", n < state.step && canReach(n + 1));
    btn.setAttribute("aria-current", n === state.step ? "step" : "false");
  }
  for (const p of $$("[data-panel]")) p.hidden = +p.dataset.panel !== state.step;
  $$("[data-panel] h2").forEach((h) => h.setAttribute("tabindex", "-1"));
  renderSources();
  renderGenerate();
  renderReview($("#review"), handlers);
  renderExport($("#export"), handlers);
}

// ---------- step 1: sources ----------
function setTab(name, focus) {
  state.tab = name;
  for (const t of $$('[role="tab"]')) {
    const on = t.id === "tab-" + name;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    if (on && focus) t.focus();
  }
  for (const p of $$('[role="tabpanel"]')) p.hidden = p.id !== "tp-" + name;
  $("#consent-box").hidden = name === "notes";
  save();
}

function addSource(src) {
  const s = Object.assign({ index: state.nextIndex, segments: [], speakers: {}, interviewer: "", thumbs: {}, status: "processing", stage: "Starting…", done: 0, total: 0 }, src);
  state.nextIndex += 1;
  state.sources.push(s);
  emit();
  return s;
}

function removeSource(src) {
  src.ctrl?.abort();
  state.sources = state.sources.filter((s) => s !== src);
  emit();
}

function progress(src, stage, done, total) {
  Object.assign(src, { stage, done, total });
  renderSources();
}

function failSource(src, err) {
  if (err?.name === "AbortError") { removeSource(src); toast("Upload cancelled"); return; }
  if (src.segments.length) { src.status = "ready"; toast(`${src.name}: stopped early — ${err.message}`, { type: "error", timeout: 8000 }); }
  else { src.status = "error"; src.error = err.message; }
  emit();
}

function finish(src, emptyMsg) {
  if (!src.segments.length) throw new Error(emptyMsg);
  src.status = "ready";
  src.ctrl = null;
  emit();
  toast(`${src.name} added — ${src.segments.length} ${src.segments.length === 1 ? "line" : "lines"}`);
}

async function addNotes({ file, text, name }) {
  const src = addSource({ name: name || file?.name || "Pasted notes", type: "notes", size: file?.size || text.length, stage: "Reading notes…" });
  try {
    const form = { source_index: src.index, kind: "notes" };
    if (file) form.file = { blob: file, name: file.name }; else form.text = text;
    src.segments = (await request("/api/notes", { form })).segments;
    finish(src, "No text was found.");
    return src;
  } catch (e) { failSource(src, e); }
}

async function addTranscript(file) {
  const src = addSource({ name: file.name, type: "transcript", size: file.size, stage: "Reading transcript…" });
  try {
    const data = await request("/api/notes", { form: { source_index: src.index, kind: "transcript", file: { blob: file, name: file.name } } });
    src.segments = data.segments;
    src.speakers = data.speakers;
    finish(src, "No speech turns were found.");
  } catch (e) { failSource(src, e); }
}

function requireConsent() {
  if ($("#consent").checked) return true;
  toast("Tick the consent box first — audio goes to a free AI service (Groq).", { type: "error" });
  $("#consent").focus();
  return false;
}

async function transcribe(src, file, kind, label) {
  progress(src, `${label} · extracting audio`, 0, 0);
  const chunks = audioChunks(await decodeTo16kMono(file, src.ctrl.signal));
  for (let i = 0; i < chunks.length; i++) {
    progress(src, `${label} · transcribing ${i + 1}/${chunks.length} parts`, i, chunks.length);
    const data = await request("/api/transcribe", { signal: src.ctrl.signal, form: {
      chunk: { blob: encodeWav(chunks[i].part), name: `part${i}.wav` }, offset_s: chunks[i].offset,
      source_index: src.index, seq_start: src.segments.length + 1, kind, consent: "true" } });
    src.segments.push(...data.segments);
  }
}

async function addInterviewMedia(file) {
  if (!requireConsent()) return;
  const src = addSource({ name: file.name, type: "interview", size: file.size, ctrl: new AbortController() });
  try {
    src.duration = await checkMedia(file);
    await transcribe(src, file, "transcript_turn", "Interview");
    finish(src, "No speech was found in this recording.");
  } catch (e) { failSource(src, e); }
}

async function addScreen(file) {
  if (!requireConsent()) return;
  const src = addSource({ name: file.name, type: "screen", size: file.size, ctrl: new AbortController() });
  let note = "";
  try {
    src.duration = await checkMedia(file);
    try { await transcribe(src, file, "narration", "Narration"); } catch (e) {
      if (e.name === "AbortError" || e.code === "daily_free_limit") throw e;
      note = "No narration found; using the screen only.";
    }
    const frames = await extractFrames(file, { signal: src.ctrl.signal, onProgress: (i, n, kept) => progress(src, `Capturing screen · ${i}/${n} moments (${kept} kept)`, i, n) });
    progress(src, "Loading screen reader…", 0, 0);
    const { readFrames } = await import("./ocr.js");
    const items = await readFrames(frames, { lang: $("#ocr-lang")?.value || "eng", signal: src.ctrl.signal,
      onProgress: (i, n) => progress(src, `Reading screen · ${i}/${n}`, i - 1, n) });
    if (items.length) {
      const data = await request("/api/frames", { signal: src.ctrl.signal, json: {
        source_index: src.index, seq_start: src.segments.length + 1, items } });
      for (const seg of data.segments) {
        const f = frames.find((x) => x.t === seg.start_s);
        if (f) src.thumbs[seg.id] = f.thumb;
      }
      src.segments.push(...data.segments);
    }
    finish(src, "Nothing readable was found in this recording.");
    if (note) toast(note);
  } catch (e) { failSource(src, e); }
}

function handleFiles(kind, files) {
  const file = files?.[0];
  if (!file) return;
  if (kind === "notes") addNotes({ file });
  else if (kind === "interview") TRANSCRIPT_EXT.test(file.name) ? addTranscript(file) : addInterviewMedia(file);
  else addScreen(file);
}

function setInterviewer(src, name) {
  src.interviewer = name;
  for (const seg of src.segments) if (src.speakers[seg.id]) seg.speaker_role = src.speakers[seg.id] === name ? "interviewer" : "staff";
  emit();
}

function toggleRole(seg) {
  seg.speaker_role = seg.speaker_role === "interviewer" ? (seg.kind === "narration" ? "narrator" : "staff") : "interviewer";
  emit();
}

const ROLE_LABEL = { staff: "Staff", interviewer: "Interviewer", narrator: "Narrator", system: "On screen", reviewer: "Reviewer" };

function openPreview(src) {
  $("#prev-title").textContent = src.name;
  const body = $("#preview-body");
  const draw = () => body.replaceChildren(...src.segments.map((seg) => {
    const toggle = ["transcript_turn", "narration"].includes(seg.kind);
    const role = toggle
      ? el("button", { type: "button", class: "role " + seg.speaker_role, title: "Switch between staff and interviewer", onclick: () => { toggleRole(seg); draw(); } }, ROLE_LABEL[seg.speaker_role])
      : el("span", { class: "role " + seg.speaker_role }, ROLE_LABEL[seg.speaker_role]);
    return el("div", { class: "tl-item" },
      el("span", { class: "mono tl-time" }, seg.start_s != null ? fmtTime(seg.start_s) : "—"),
      el("div", {}, role, src.speakers[seg.id] ? el("b", {}, src.speakers[seg.id] + ": ") : null, seg.text),
      src.thumbs?.[seg.id] ? el("img", { src: src.thumbs[seg.id], alt: `Screen at ${fmtTime(seg.start_s)}` }) : null);
  }));
  draw();
  openDialog("dlg-preview");
}

function sourceCard(src) {
  const t = TYPE[src.type];
  const names = [...new Set(Object.values(src.speakers || {}))];
  const meta = [t.label, fmtSize(src.size), src.duration ? fmtTime(src.duration) : ""].filter(Boolean);
  const pct = src.total ? Math.round((src.done / src.total) * 100) : 0;
  return el("li", { class: "src " + src.status },
    el("div", { class: "src-top" },
      el("span", { class: "src-ic" }, icon(t.icon)),
      el("div", { class: "src-main" },
        el("div", { class: "src-name", title: src.name }, src.name),
        el("div", { class: "src-meta" }, meta.map((m, i) => el("span", { class: i ? "mono" : "" }, m)))),
      el("button", { type: "button", class: "btn ghost sm icon-only", "aria-label": (src.status === "processing" ? "Cancel " : "Remove ") + src.name, onclick: () => removeSource(src) }, icon(src.status === "processing" ? "x" : "trash"))),
    src.status === "processing" ? [
      el("div", { class: "progress" + (src.total ? "" : " indet"), role: "progressbar", "aria-label": src.stage, "aria-valuenow": src.total ? pct : null, "aria-valuemin": 0, "aria-valuemax": 100 }, el("i", { style: `--p:${Math.max(4, pct)}%` })),
      el("div", { class: "stage" }, src.stage)] : null,
    src.status === "error" ? el("p", { class: "src-err", role: "alert" }, src.error) : null,
    src.status === "ready" ? el("div", { class: "src-actions" },
      el("span", { class: "ok-tick small" }, icon("check"), `${src.segments.length} ${src.segments.length === 1 ? "line" : "lines"}`),
      el("button", { type: "button", class: "btn ghost sm", onclick: () => openPreview(src) }, icon("eye"), "Preview"),
      names.length ? el("label", { class: "small" }, el("span", { class: "sr-only" }, "Who is the interviewer?"),
        el("select", { "aria-label": "Who is the interviewer?", onchange: (e) => setInterviewer(src, e.target.value) },
          el("option", { value: "" }, "Who is the interviewer?"),
          names.map((n) => el("option", { value: n, selected: n === src.interviewer }, n)))) : null) : null);
}

function renderSources() {
  const list = $("#source-list");
  list.replaceChildren(...state.sources.map(sourceCard));
  const ready = readySources().length;
  $("#sources-empty").hidden = state.sources.length > 0;
  $("#sources-count").textContent = state.sources.length;
  $("#to-step2").disabled = !ready;
  $("#p1-hint").textContent = ready ? `${ready} source${ready > 1 ? "s" : ""} ready` : state.sources.length ? "Waiting for your sources to finish…" : "Add at least one source to continue.";
}

// ---------- step 2: generate ----------
function renderGenerate() {
  $("#title-hint").value = state.title || "";
  $("#gen-sources").replaceChildren(...readySources().map((s) => el("span", { class: "chip" }, icon(TYPE[s.type].icon), s.name)));
}

const STAGE_MS = [500, 0, 0, 0];
async function generate(button) {
  if (!readySources().length) { goStep(1); return; }
  state.title = $("#title-hint").value.trim();
  const payload = { title_hint: state.title, segments: allSegments(), answers: committedAnswers() };
  if (JSON.stringify(payload).length > 4e6) { showGenError({ code: "too_large", message: "There's too much source material (over 4 MB). Remove a source and try again." }); return; }
  $("#gen-error").replaceChildren();
  const stages = $$("#stages li");
  stages.forEach((li) => li.className = "");
  $("#gen-progress").hidden = false;
  stages[0].className = "active";
  const t1 = setTimeout(() => { stages[0].className = "done"; stages[1].className = "active"; }, STAGE_MS[0]);
  const t2 = setTimeout(() => { stages[1].className = "done"; stages[2].className = "active"; }, 9000);
  try {
    const data = await busy(button, () => request("/api/generate", { json: payload }));
    clearTimeout(t1); clearTimeout(t2);
    stages.forEach((li) => li.className = "done");
    const old = state.answers;
    state.sop = data.sop;
    state.report = data.report;
    if (data.report?.notes?.length) toast(data.report.notes[0]);
    state.version += 1;
    state.answers = {};
    for (const q of state.sop.questions) {
      const prev = Object.values(old).find((a) => a.question === q.question);
      if (prev) state.answers[q.id] = prev;
    }
    state.drafts = {};
    state.edited = {};
    state.filter = "all";
    state.genSig = answersSig();
    await new Promise((r) => setTimeout(r, 300));
    $("#gen-progress").hidden = true;
    goStep(3);
    toast(`Draft ready — ${state.sop.steps.length} steps, ${state.sop.questions.length} questions`);
  } catch (e) {
    clearTimeout(t1); clearTimeout(t2);
    $("#gen-progress").hidden = true;
    showGenError(e);
  }
}

function showGenError(e) {
  const kinds = {
    insufficient_source: ["This doesn't look like a procedure yet", "We need at least two actions. Add notes, an interview or a narrated recording that walks through the steps.", "Add more sources", () => goStep(1)],
    daily_free_limit: ["Free AI limit reached", "Free AI limit reached, try again in a minute. Your work is saved in this browser.", null],
  };
  const [title, text, label, action] = kinds[e.code] || ["Generation didn't finish", e.message, "Try again", () => generate($("#generate"))];
  $("#gen-error").replaceChildren(el("div", { class: "err-card" + (e.code === "daily_free_limit" ? " warn" : ""), role: "alert" },
    icon("alert"), el("div", {}, el("h3", {}, title), el("p", {}, text), label ? el("button", { type: "button", class: "btn secondary", onclick: action }, icon(e.code === "insufficient_source" ? "back" : "refresh"), label) : null)));
}

async function regenerate(button) {
  goStep(2, { focus: false });
  await generate($("#generate"));
  button?.blur();
}

// ---------- sample, theme, session ----------
async function trySample(button) {
  await busy(button, async () => {
    try {
      const sample = await request("/api/sample");
      state.started = true;
      renderChrome();
      const src = await addNotes({ text: sample.text, name: "Sample: refund notes" });
      if (!src) return;
      state.title = sample.title;
      goStep(2, { focus: false });
      await generate($("#generate"));
    } catch (e) { toast(e.message, { type: "error" }); }
  });
}

function effectiveTheme() {
  return document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function paintThemeButton() {
  const dark = effectiveTheme() === "dark", btn = $("#theme-btn");
  btn.replaceChildren(icon(dark ? "sun" : "moon"));
  btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}
function toggleTheme() {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("sop-theme", next); } catch { /* ignore */ }
  paintThemeButton();
}

async function startOver() {
  if (!(await confirmDialog("Start a new SOP?", "This clears your sources, the draft and your answers from this browser. Download a session file first if you want to keep them.", "Clear and start over"))) return;
  state.sources.forEach((s) => s.ctrl?.abort());
  replace(fresh());
  clearSaved();
  renderChrome();
  window.scrollTo({ top: 0 });
  toast("Started a new SOP");
}

async function loadSessionFile(file) {
  try {
    const data = JSON.parse(await file.text());
    if (!data || !Array.isArray(data.sources)) throw new Error();
    replace(data);
    state.started = true;
    if (!canReach(state.step)) state.step = 1;
    emit();
    renderChrome();
    toast("Session loaded");
  } catch { toast("That file isn't an SOP Builder session.", { type: "error" }); }
}

// ---------- wiring ----------
function wire() {
  $("#start-btn").addEventListener("click", () => goStep(1));
  $("#sample-btn").addEventListener("click", (e) => trySample(e.currentTarget));
  $("#brand").addEventListener("click", (e) => { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); });
  $("#how-btn").addEventListener("click", () => openDialog("dlg-how"));
  $("#theme-btn").addEventListener("click", toggleTheme);
  $("#save-session").addEventListener("click", () => { $("#session-menu").hidePopover?.(); saveSession(); });
  $("#load-session").addEventListener("change", (e) => { $("#session-menu").hidePopover?.(); const f = e.target.files[0]; if (f) loadSessionFile(f); e.target.value = ""; });
  $$("[data-open]").forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); openDialog(b.dataset.open); }));
  $$("#stepper button").forEach((b) => b.addEventListener("click", () => goStep(+b.dataset.step)));
  $$("[data-go]").forEach((b) => b.addEventListener("click", () => goStep(+b.dataset.go)));
  $("#to-step2").addEventListener("click", () => goStep(2));
  $("#generate").addEventListener("click", (e) => generate(e.currentTarget));
  $("#title-hint").addEventListener("input", (e) => { state.title = e.target.value; save(); });

  const tabs = $$('[role="tab"]');
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => setTab(t.id.slice(4)));
    t.addEventListener("keydown", (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (d) { e.preventDefault(); setTab(tabs[(i + d + tabs.length) % tabs.length].id.slice(4), true); }
    });
  });

  const notes = $("#notes-text");
  notes.addEventListener("input", () => { $("#notes-count").textContent = `${notes.value.length.toLocaleString()} characters`; });
  $("#add-notes").addEventListener("click", () => {
    const text = notes.value.trim();
    if (!text) { toast("Paste some notes or drop a file first.", { type: "error" }); notes.focus(); return; }
    addNotes({ text }).then((s) => { if (s) { notes.value = ""; notes.dispatchEvent(new Event("input")); } });
  });

  for (const zone of $$(".drop")) {
    const kind = zone.dataset.kind, input = $("input", zone);
    input.addEventListener("change", () => { handleFiles(kind, input.files); input.value = ""; });
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); handleFiles(kind, e.dataTransfer.files); });
  }

  document.addEventListener("paste", (e) => {
    if (state.step !== 1 || !state.started || e.target.closest("input, textarea")) return;
    const files = e.clipboardData?.files;
    if (files?.length) { e.preventDefault(); handleFiles(state.tab, files); return; }
    const text = e.clipboardData?.getData("text");
    if (text && state.tab === "notes") { e.preventDefault(); notes.value += text; notes.dispatchEvent(new Event("input")); notes.focus(); }
  });

  document.addEventListener("keydown", (e) => {
    const typing = e.target.closest("input, textarea, select, [contenteditable]");
    if (e.key === "/" && !typing && !document.querySelector("dialog[open]")) {
      e.preventDefault();
      if (!state.started) state.started = true;
      if (state.step !== 1) goStep(1, { focus: false }); else renderChrome();
      setTab("notes");
      setTimeout(() => notes.focus(), 60);
    }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && readySources().length && !$("#generate").classList.contains("loading")) {
      e.preventDefault();
      if (state.step !== 2) goStep(2, { focus: false });
      generate($("#generate"));
    }
  });

  bus.addEventListener("change", renderChrome);
  bus.addEventListener("review", renderChrome);
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", paintThemeButton);
}

function reveal() {
  const items = $$(".reveal");
  if (!("IntersectionObserver" in window) || matchMedia("(prefers-reduced-motion: reduce)").matches) { items.forEach((i) => i.classList.add("in")); return; }
  const io = new IntersectionObserver((entries) => entries.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } }), { rootMargin: "0px 0px -40px 0px" });
  items.forEach((i) => io.observe(i));
}

function init() {
  wire();
  paintThemeButton();
  const saved = loadSaved();
  if (saved && (saved.sources?.length || saved.sop)) {
    replace(saved);
    toast("Restored your previous session", { action: "Undo", timeout: 8000, onAction: () => { replace(fresh()); clearSaved(); renderChrome(); } });
  }
  if (!canReach(state.step)) state.step = 1;
  setTab(state.tab || "notes");
  renderChrome();
  reveal();
}

init();
