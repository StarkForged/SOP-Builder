"use strict";
// SOP Builder front end: all work-in-progress lives in this browser tab (+ optional localStorage).

const STORE_KEY = "sop-builder-session-v1";
const MAX_MEDIA_BYTES = 200 * 1024 * 1024;
const MAX_MEDIA_SECONDS = 30 * 60;
const CHUNK_SECONDS = 100;
const SAMPLE_RATE = 16000;
const FRAME_EVERY_S = 3;
const MAX_FRAMES = 40;
const FRAME_BATCH = 6;
const FRAME_BATCH_BYTES = 3.5 * 1000 * 1000;
const DECODE_HELP = "Your browser cannot decode this file. Please convert it to MP4 (H.264/AAC) and try again.";

let state = freshState();

function freshState() {
  return { sources: [], nextIndex: 1, titleHint: "", sop: null, report: null, answers: {}, reviewer: "" };
}

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function fmtTime(s) {
  if (s == null) return "";
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? h + ":" : "") + String(m).padStart(h ? 2 : 1, "0") + ":" + String(sec).padStart(2, "0");
}

function setText(id, text) { $(id).textContent = text || ""; }

async function api(path, options) {
  let resp;
  try {
    resp = await fetch(path, options);
  } catch (e) {
    throw new Error("Network error: could not reach the server.");
  }
  if (resp.ok) return resp;
  let data = {};
  try { data = await resp.json(); } catch (e) { /* not JSON */ }
  if (data.error === "daily_free_limit") throw new Error("Free AI limit reached, try again later.");
  throw new Error(data.message || data.error || `Request failed (${resp.status}).`);
}

async function postForm(path, fields) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields)) {
    if (Array.isArray(v)) v.forEach((item) => fd.append(k, item.blob, item.name));
    else if (v && v.blob) fd.append(k, v.blob, v.name);
    else fd.append(k, v);
  }
  return (await api(path, { method: "POST", body: fd })).json();
}

function save() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* storage unavailable */ }
}

function load() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) state = Object.assign(freshState(), JSON.parse(raw));
  } catch (e) { /* ignore */ }
}

function requireConsent() {
  if (!$("consent").checked) throw new Error("Please tick the consent box before uploading audio or video.");
}

function allSegments() {
  return state.sources.flatMap((s) => s.segments);
}

// ---------- sources ----------
function addSource(name, type, segments, speakers) {
  const src = { index: state.nextIndex, name, type, segments, speakers: speakers || {}, interviewer: "" };
  state.nextIndex += 1;
  state.sources.push(src);
  renderSources();
  save();
  return src;
}

function setInterviewer(src, name) {
  src.interviewer = name;
  for (const seg of src.segments) {
    const sp = src.speakers[seg.id];
    if (sp) seg.speaker_role = sp === name ? "interviewer" : "staff";
  }
  renderSources();
  save();
}

function toggleRole(seg) {
  seg.speaker_role = seg.speaker_role === "interviewer" ? (seg.kind === "narration" ? "narrator" : "staff") : "interviewer";
  renderSources();
  save();
}

function renderSources() {
  const list = $("source-list");
  list.innerHTML = "";
  if (!state.sources.length) {
    list.append(el("li", { class: "muted" }, "No sources yet."));
    return;
  }
  for (const src of state.sources) {
    const names = [...new Set(Object.values(src.speakers))];
    const segRows = src.segments.map((seg) => {
      const toggle = ["transcript_turn", "narration"].includes(seg.kind)
        ? el("button", { class: "link", onclick: () => toggleRole(seg) }, seg.speaker_role)
        : el("span", { class: "muted" }, seg.speaker_role);
      const sp = src.speakers[seg.id] ? src.speakers[seg.id] + ": " : "";
      return el("div", {}, el("span", { class: "muted" }, `${seg.id} ${fmtTime(seg.start_s)} `), toggle, " ", sp + seg.text);
    });
    list.append(el("li", {},
      el("div", { class: "row" },
        el("strong", {}, `#${src.index} ${src.name}`),
        el("span", { class: "muted" }, ` — ${src.type}, ${src.segments.length} segments`),
        el("button", { class: "link", onclick: () => removeSource(src.index) }, "Remove")),
      names.length ? el("label", {}, "Interviewer: ",
        el("select", { onchange: (e) => setInterviewer(src, e.target.value) },
          el("option", { value: "" }, "(none)"),
          names.map((n) => el("option", { value: n, selected: n === src.interviewer }, n)))) : null,
      el("details", {}, el("summary", {}, "Show segments"), el("div", { class: "seglist" }, segRows))));
  }
}

function removeSource(index) {
  state.sources = state.sources.filter((s) => s.index !== index);
  renderSources();
  save();
}

async function withBusy(button, errorId, fn) {
  setText(errorId, "");
  button.disabled = true;
  try {
    await fn();
  } catch (e) {
    setText(errorId, e.message || String(e));
  } finally {
    button.disabled = false;
    setText("progress", "");
  }
}

async function addNotes() {
  const file = $("notes-file").files[0];
  const text = $("notes-text").value;
  if (!file && !text.trim()) throw new Error("Paste some notes or choose a file.");
  const fields = { source_index: state.nextIndex, kind: "notes" };
  if (file) fields.file = { blob: file, name: file.name }; else fields.text = text;
  const data = await postForm("/api/notes", fields);
  addSource(file ? file.name : "Pasted notes", "notes", data.segments);
  $("notes-text").value = "";
  $("notes-file").value = "";
}

async function addTranscript() {
  const file = $("transcript-file").files[0];
  if (!file) throw new Error("Choose a transcript file.");
  const data = await postForm("/api/notes", { source_index: state.nextIndex, kind: "transcript", file: { blob: file, name: file.name } });
  addSource(file.name, "interview transcript", data.segments, data.speakers);
  $("transcript-file").value = "";
}

// ---------- media ----------
function mediaDuration(file) {
  return new Promise((resolve, reject) => {
    const isVideo = file.type.startsWith("video") || /\.(mp4|mov|webm)$/i.test(file.name);
    const m = document.createElement(isVideo ? "video" : "audio");
    const url = URL.createObjectURL(file);
    m.preload = "metadata";
    m.onloadedmetadata = () => { URL.revokeObjectURL(url); resolve(m.duration); };
    m.onerror = () => { URL.revokeObjectURL(url); reject(new Error(DECODE_HELP)); };
    m.src = url;
  });
}

async function checkMedia(file) {
  if (file.size > MAX_MEDIA_BYTES) throw new Error("File is larger than 200 MB.");
  const duration = await mediaDuration(file);
  if (isFinite(duration) && duration > MAX_MEDIA_SECONDS) throw new Error("Recording is longer than 30 minutes.");
  return duration;
}

async function decodeTo16kMono(file) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx || !window.OfflineAudioContext) throw new Error("This browser cannot process audio. Try Chrome, Edge or Firefox.");
  const ctx = new Ctx();
  let audio;
  try {
    audio = await ctx.decodeAudioData(await file.arrayBuffer());
  } catch (e) {
    throw new Error(DECODE_HELP);
  } finally {
    ctx.close();
  }
  const length = Math.max(1, Math.ceil(audio.duration * SAMPLE_RATE));
  const off = new OfflineAudioContext(1, length, SAMPLE_RATE);
  const src = off.createBufferSource();
  src.buffer = audio;
  src.connect(off.destination);
  src.start();
  const rendered = await off.startRendering();
  return rendered.getChannelData(0);
}

function encodeWav(samples) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, SAMPLE_RATE, true); v.setUint32(28, SAMPLE_RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

function rms(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i += 4) sum += samples[i] * samples[i];
  return Math.sqrt(sum / Math.max(1, samples.length / 4));
}

// Transcribe audio of `file` into `src` (segments appended). Returns number of segments added.
async function transcribeInto(src, file, kind, label) {
  setText("progress", `${label}: decoding audio…`);
  const samples = await decodeTo16kMono(file);
  const per = CHUNK_SECONDS * SAMPLE_RATE;
  const chunks = Math.ceil(samples.length / per);
  let added = 0;
  for (let i = 0; i < chunks; i++) {
    const part = samples.subarray(i * per, Math.min(samples.length, (i + 1) * per));
    if (rms(part) < 0.003) continue; // silence: skip so the speech model does not hallucinate
    setText("progress", `${label}: transcribing chunk ${i + 1} of ${chunks}…`);
    const data = await postForm("/api/transcribe", {
      chunk: { blob: encodeWav(part), name: `chunk${i}.wav` }, offset_s: i * CHUNK_SECONDS,
      source_index: src.index, seq_start: src.segments.length + 1, kind, consent: "true",
    });
    src.segments.push(...data.segments);
    added += data.segments.length;
    renderSources();
    save();
  }
  return added;
}

async function addInterviewMedia() {
  requireConsent();
  const file = $("interview-media").files[0];
  if (!file) throw new Error("Choose an audio or video file.");
  await checkMedia(file);
  const src = addSource(file.name, "interview recording", []);
  try {
    const n = await transcribeInto(src, file, "transcript_turn", "Interview");
    if (!n) throw new Error("No speech was found in this recording.");
  } catch (e) {
    if (!src.segments.length) removeSource(src.index);
    throw e;
  }
  $("interview-media").value = "";
}

function loadVideo(file) {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    v.muted = true;
    v.preload = "auto";
    v.playsInline = true;
    v.onloadeddata = () => (v.videoWidth ? resolve(v) : reject(new Error(DECODE_HELP)));
    v.onerror = () => reject(new Error(DECODE_HELP));
    v.src = URL.createObjectURL(file);
  });
}

function seek(video, t) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => done(), 4000);
    const done = () => { clearTimeout(timer); video.removeEventListener("seeked", done); resolve(); };
    video.addEventListener("seeked", done);
    video.currentTime = t;
  });
}

function toBlob(canvas, quality) {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
}

async function extractFrames(file) {
  const video = await loadVideo(file);
  const duration = video.duration;
  const scale = Math.min(1, 1280 / video.videoWidth);
  const canvas = Object.assign(document.createElement("canvas"), { width: Math.round(video.videoWidth * scale), height: Math.round(video.videoHeight * scale) });
  const thumb = Object.assign(document.createElement("canvas"), { width: 32, height: 32 });
  const ctx = canvas.getContext("2d");
  const tctx = thumb.getContext("2d", { willReadFrequently: true });
  const frames = [];
  let prev = null;
  try {
    for (let t = 0; t < duration && frames.length < MAX_FRAMES; t += FRAME_EVERY_S) {
      setText("progress", `Screen: reading frame at ${fmtTime(t)} (${frames.length} kept)…`);
      await seek(video, t);
      tctx.drawImage(video, 0, 0, 32, 32);
      const px = tctx.getImageData(0, 0, 32, 32).data;
      if (prev) {
        let diff = 0;
        for (let i = 0; i < px.length; i += 4) diff += Math.abs(px[i] - prev[i]) + Math.abs(px[i + 1] - prev[i + 1]) + Math.abs(px[i + 2] - prev[i + 2]);
        if (diff / (32 * 32 * 3) < 3) continue; // near-identical to the last kept frame
      }
      prev = px;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      let blob = await toBlob(canvas, 0.8);
      if (blob.size > 1_000_000) blob = await toBlob(canvas, 0.6);
      frames.push({ t: Math.round(t * 10) / 10, blob });
    }
  } finally {
    URL.revokeObjectURL(video.src);
  }
  return frames;
}

async function addScreen() {
  requireConsent();
  const file = $("screen-file").files[0];
  if (!file) throw new Error("Choose a screen recording.");
  await checkMedia(file);
  const src = addSource(file.name, "screen recording", []);
  const notes = [];
  try {
    try {
      await transcribeInto(src, file, "narration", "Narration");
    } catch (e) {
      if (/Free AI limit/.test(e.message)) throw e;
      notes.push("No narration transcribed (" + e.message + ")");
    }
    const frames = await extractFrames(file);
    const batches = [];
    let cur = [], size = 0;
    for (const f of frames) {
      if (cur.length >= FRAME_BATCH || size + f.blob.size > FRAME_BATCH_BYTES) { batches.push(cur); cur = []; size = 0; }
      cur.push(f); size += f.blob.size;
    }
    if (cur.length) batches.push(cur);
    for (let b = 0; b < batches.length; b++) {
      setText("progress", `Screen: reading text from screenshots, batch ${b + 1} of ${batches.length}…`);
      const batch = batches[b];
      const data = await postForm("/api/frames", {
        frames: batch.map((f, i) => ({ blob: f.blob, name: `frame${i}.jpg` })), times: JSON.stringify(batch.map((f) => f.t)),
        source_index: src.index, seq_start: src.segments.length + 1, consent: "true",
      });
      src.segments.push(...data.segments);
      renderSources();
      save();
    }
    if (!src.segments.length) throw new Error("Nothing readable was found in this recording.");
  } catch (e) {
    if (!src.segments.length) removeSource(src.index);
    throw e;
  }
  if (notes.length) setText("sources-error", notes.join("\n"));
  $("screen-file").value = "";
}

// ---------- generate & review ----------
function answerPayload() {
  const out = [];
  for (const q of (state.sop ? state.sop.questions : [])) {
    const a = state.answers[q.id];
    if (!a) continue;
    const text = a.na ? `Not applicable: ${a.reason || "no reason given"}` : (a.answer || "").trim();
    if (text) out.push({ question_id: q.id, question: q.question, answer: text });
  }
  return out;
}

async function generate() {
  const segments = allSegments();
  if (!segments.length) throw new Error("Add at least one source first.");
  state.titleHint = $("title-hint").value;
  const body = JSON.stringify({ title_hint: state.titleHint, segments, answers: answerPayload() });
  if (body.length > 4_000_000) throw new Error("Too much source material (max 4 MB). Remove a source and try again.");
  setText("generate-status", "Generating… this can take up to a minute.");
  try {
    const data = await (await api("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body })).json();
    const old = state.answers;
    state.sop = data.sop;
    state.report = data.report;
    state.answers = {};
    // keep answers whose question text survived regeneration
    for (const q of state.sop.questions) {
      const prev = Object.values(old).find((a) => a.question === q.question);
      if (prev) state.answers[q.id] = prev;
    }
    renderReview();
    save();
    setText("generate-status", `Done (model: ${data.report.model}).`);
  } catch (e) {
    setText("generate-status", "");
    throw e;
  }
}

const STATUS_TEXT = { supported: "supported", inferred: "inferred", needs_confirmation: "needs confirmation" };
const badge = (status) => el("span", { class: "badge " + status }, STATUS_TEXT[status] || status);

function refsView(refs) {
  const segs = Object.fromEntries(allSegments().map((s) => [s.id, s]));
  if (!refs.length) return el("div", { class: "refs muted" }, "No source quotes.");
  return el("ul", { class: "refs" }, refs.map((r) => {
    const s = segs[r.segment_id];
    const when = s && s.start_s != null ? ` @ ${fmtTime(s.start_s)}` : "";
    return el("li", {}, `${r.segment_id}${when}: “${r.quote}”`);
  }));
}

function stepView(step) {
  const details = el("div", { class: "hidden" });
  const textNode = el("span", {}, step.action);
  const editBox = el("div", { class: "hidden" });
  const fill = () => {
    details.innerHTML = "";
    if (step.actor) details.append(el("div", { class: "muted" }, "Actor: " + step.actor));
    if (step.expected_result) details.append(el("div", { class: "muted" }, "Expected result: " + step.expected_result));
    if (step.ui_elements.length) details.append(el("div", { class: "muted" }, "UI: " + step.ui_elements.join(", ")));
    details.append(refsView(step.source_refs));
  };
  const area = el("textarea", { rows: 2 });
  const confirmed = el("input", { type: "checkbox" });
  editBox.append(area, el("label", {}, confirmed, " Confirmed by staff"),
    el("button", { onclick: (e) => {
      e.stopPropagation();
      const v = area.value.trim();
      if (v && v !== step.action) {
        step.action = v;
        step.status = confirmed.checked ? "supported" : "needs_confirmation";
      } else if (confirmed.checked) step.status = "supported";
      save();
      renderReview();
    } }, "Save"),
    el("button", { onclick: (e) => { e.stopPropagation(); editBox.classList.add("hidden"); } }, "Cancel"));
  editBox.addEventListener("click", (e) => e.stopPropagation());
  return el("li", { class: "step", onclick: () => { fill(); details.classList.toggle("hidden"); } },
    el("strong", {}, `${step.number}. `),
    step.kind === "decision" ? el("em", {}, `Decision${step.condition ? " – if " + step.condition : ""}: `) : null,
    step.kind === "verification" ? el("em", {}, "Check: ") : null,
    textNode, badge(step.status),
    el("button", { class: "link", onclick: (e) => { e.stopPropagation(); area.value = step.action; confirmed.checked = false; editBox.classList.remove("hidden"); } }, "Edit"),
    editBox, details);
}

function questionView(q) {
  const a = state.answers[q.id] || (state.answers[q.id] = { question: q.question, answer: "", na: false, reason: "" });
  a.question = q.question;
  const answer = el("textarea", { rows: 2, placeholder: "Answer from staff…" });
  answer.value = a.answer;
  answer.addEventListener("input", () => { a.answer = answer.value; save(); });
  const reason = el("input", { type: "text", placeholder: "Reason it does not apply", class: a.na ? "" : "hidden" });
  reason.value = a.reason;
  reason.addEventListener("input", () => { a.reason = reason.value; save(); });
  const na = el("input", { type: "checkbox", checked: a.na });
  na.addEventListener("change", () => { a.na = na.checked; reason.classList.toggle("hidden", !na.checked); answer.disabled = na.checked; save(); });
  answer.disabled = a.na;
  return el("li", {},
    el("div", {}, el("strong", {}, q.question), el("span", { class: "badge " + q.severity }, q.severity),
      el("span", { class: "muted" }, ` ${q.category.replace(/_/g, " ")}`)),
    el("div", { class: "muted" }, q.reason),
    answer, el("label", {}, na, " Not applicable"), reason);
}

function renderReview() {
  const box = $("review");
  box.innerHTML = "";
  const sop = state.sop;
  if (!sop) {
    box.append(el("p", { class: "muted" }, "Generate an SOP to review it here."));
    return;
  }
  const simpleItems = (items, text) => items.length
    ? el("ul", { class: "list" }, items.map((it) => {
      const refs = el("div", { class: "hidden" }, refsView(it.source_refs));
      return el("li", { class: "step", onclick: () => refs.classList.toggle("hidden") }, text(it), badge(it.status), refs);
    }))
    : el("p", { class: "muted" }, "None.");
  const mandatoryOpen = sop.questions.filter((q) => q.severity === "mandatory").length;
  box.append(
    el("h3", {}, sop.title),
    el("p", {}, sop.purpose),
    el("h3", {}, "Prerequisites"), simpleItems(sop.prerequisites, (p) => p.text),
    el("h3", {}, "Steps ", el("span", { class: "muted" }, "(click a step for its sources)")),
    el("ul", { class: "list" }, sop.steps.map(stepView)),
    el("h3", {}, "Warnings"), simpleItems(sop.warnings, (w) => `${w.severity.toUpperCase()}: ${w.text}`),
    el("h3", {}, `Questions for staff (${sop.questions.length}, ${mandatoryOpen} mandatory)`),
    sop.questions.length ? el("ul", { class: "list" }, sop.questions.map(questionView)) : el("p", { class: "muted" }, "No open questions."),
    el("button", { class: "primary", onclick: (e) => withBusy(e.target, "generate-error", generate) }, "Regenerate with answers"),
    el("h3", {}, "Review checklist"),
    el("ul", {}, sop.checklist.map((c) => el("li", {}, "☐ " + c.text))));
}

// ---------- export & session ----------
function download(blob, name) {
  const a = el("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

async function exportDoc(format) {
  if (!state.sop) throw new Error("Generate an SOP first.");
  state.reviewer = $("reviewer").value;
  save();
  const resp = await api("/api/export", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sop: state.sop, answers: answerPayload(), reviewer_name: state.reviewer, format, segments: allSegments() }),
  });
  const cd = resp.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename="([^"]+)"/);
  download(await resp.blob(), m ? m[1] : `sop.${format}`);
}

function saveSession() {
  state.reviewer = $("reviewer").value;
  state.titleHint = $("title-hint").value;
  download(new Blob([JSON.stringify(state, null, 1)], { type: "application/json" }), "sop-session.json");
}

async function loadSession(file) {
  const data = JSON.parse(await file.text());
  if (!data || !Array.isArray(data.sources)) throw new Error("This is not an SOP Builder session file.");
  state = Object.assign(freshState(), data);
  renderAll();
  save();
}

function renderAll() {
  $("title-hint").value = state.titleHint || "";
  $("reviewer").value = state.reviewer || "";
  renderSources();
  renderReview();
}

// ---------- wiring ----------
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
  document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("hidden", p.id !== "tab-" + tab.dataset.tab));
}));
$("add-notes").addEventListener("click", (e) => withBusy(e.target, "sources-error", addNotes));
$("add-transcript").addEventListener("click", (e) => withBusy(e.target, "sources-error", addTranscript));
$("add-interview-media").addEventListener("click", (e) => withBusy(e.target, "sources-error", addInterviewMedia));
$("add-screen").addEventListener("click", (e) => withBusy(e.target, "sources-error", addScreen));
$("generate").addEventListener("click", (e) => withBusy(e.target, "generate-error", generate));
$("export-docx").addEventListener("click", (e) => withBusy(e.target, "export-error", () => exportDoc("docx")));
$("export-md").addEventListener("click", (e) => withBusy(e.target, "export-error", () => exportDoc("md")));
$("save-session").addEventListener("click", saveSession);
$("load-session").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (f) loadSession(f).catch((err) => setText("export-error", err.message)).finally(() => { e.target.value = ""; });
});
$("clear-session").addEventListener("click", () => {
  if (!confirm("Remove all sources, the SOP and answers from this browser?")) return;
  state = freshState();
  try { localStorage.removeItem(STORE_KEY); } catch (e) { /* ignore */ }
  renderAll();
});
$("title-hint").addEventListener("input", () => { state.titleHint = $("title-hint").value; save(); });
$("reviewer").addEventListener("input", () => { state.reviewer = $("reviewer").value; save(); });

load();
renderAll();
