// App state: lives in memory, autosaved to localStorage when available (never required).
const KEY = "sop-builder-session-v2";

export function fresh() {
  return {
    started: false, step: 1, tab: "notes", sources: [], nextIndex: 1, title: "",
    sop: null, report: null, answers: {}, drafts: {}, genSig: "", edited: {},
    reviewer: "", version: 0, filter: "all",
  };
}

export const state = fresh();

export function replace(obj) {
  for (const k of Object.keys(state)) delete state[k];
  Object.assign(state, fresh(), obj || {});
  // Work that was in progress when the page closed cannot resume.
  for (const s of state.sources) {
    if (s.status === "processing") { s.status = "error"; s.error = "Interrupted. Remove it and add the file again."; }
  }
}

let timer = 0;
export function save() {
  clearTimeout(timer);
  timer = setTimeout(() => {
    try {
      const copy = { ...state, sources: state.sources.map(({ ctrl, ...s }) => s) };
      localStorage.setItem(KEY, JSON.stringify(copy));
    } catch { /* storage full or blocked: the app still works */ }
  }, 250);
}

export function loadSaved() {
  try { const raw = localStorage.getItem(KEY); return raw ? JSON.parse(raw) : null; } catch { return null; }
}

export function clearSaved() {
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
}

export const bus = new EventTarget();
export function emit(type = "change") {
  bus.dispatchEvent(new Event(type));
  save();
}

export const readySources = () => state.sources.filter((s) => s.status === "ready");
export const allSegments = () => readySources().flatMap((s) => s.segments);
export const segmentMap = () => Object.fromEntries(allSegments().map((s) => [s.id, s]));

export function committedAnswers() {
  const out = [];
  for (const q of state.sop?.questions || []) {
    const a = state.answers[q.id];
    if (!a) continue;
    const text = a.na ? `Not applicable: ${a.reason || "no reason given"}` : (a.answer || "").trim();
    if (text) out.push({ question_id: q.id, question: q.question, answer: text });
  }
  return out;
}

export const answersSig = () => JSON.stringify(committedAnswers().map((a) => [a.question, a.answer]));
export const isAnswered = (q) => committedAnswers().some((a) => a.question_id === q.id);
export const openMandatory = () => (state.sop?.questions || []).filter((q) => q.severity === "mandatory" && !isAnswered(q));
