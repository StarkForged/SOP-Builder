// In-browser screen reading with Tesseract.js (runs in a Web Worker). Loaded only when a screen recording is added.
// Screenshots never leave the browser; only the recognised text is sent to the server.
const TESSERACT_URL = "https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js";
export const MIN_CONFIDENCE = 50;
export const MIN_CHARS = 3;

let loading = null;

function loadTesseract() {
  if (window.Tesseract) return Promise.resolve(window.Tesseract);
  loading ||= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = TESSERACT_URL;
    s.async = true;
    s.crossOrigin = "anonymous";
    s.onload = () => resolve(window.Tesseract);
    s.onerror = () => {
      loading = null;
      s.remove();
      reject(new Error("Couldn't load the screen reader (Tesseract.js). Check your connection and try again."));
    };
    document.head.append(s);
  });
  return loading;
}

/** Keep lines with confidence >= 50 and at least 3 characters; returns {text, confidence} or null. */
export function cleanResult(data) {
  const lines = data?.lines?.length ? data.lines : [{ text: data?.text || "", confidence: data?.confidence || 0 }];
  const kept = lines
    .map((l) => ({ text: String(l.text || "").replace(/\s+/g, " ").trim(), confidence: Number(l.confidence) || 0 }))
    .filter((l) => l.confidence >= MIN_CONFIDENCE && l.text.length >= MIN_CHARS);
  if (!kept.length) return null;
  const confidence = kept.reduce((sum, l) => sum + l.confidence, 0) / kept.length;
  return { text: kept.map((l) => l.text).join("\n"), confidence: Math.round(confidence * 10) / 10 };
}

/** OCR each keyframe; returns [{t, text, confidence}] for frames with readable text. */
export async function readFrames(frames, { lang = "eng", signal, onProgress } = {}) {
  const Tesseract = await loadTesseract();
  if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
  const worker = await Tesseract.createWorker(lang, 1);
  const stop = () => worker.terminate();
  signal?.addEventListener("abort", stop, { once: true });
  const items = [];
  try {
    for (let i = 0; i < frames.length; i++) {
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      onProgress?.(i + 1, frames.length);
      const { data } = await worker.recognize(frames[i].blob);
      const r = cleanResult(data);
      if (r) items.push({ t: frames[i].t, ...r });
    }
  } catch (e) {
    if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
    throw e;
  } finally {
    signal?.removeEventListener("abort", stop);
    if (!signal?.aborted) await worker.terminate().catch(() => {});
  }
  return items;
}
