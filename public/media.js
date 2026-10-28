// Browser-side media processing: audio -> 16 kHz mono WAV chunks, video -> keyframe JPEGs.
export const LIMITS = { bytes: 200 * 1024 * 1024, seconds: 30 * 60, chunkS: 100, rate: 16000, frameEvery: 3, maxFrames: 40 };
export const DECODE_HELP = "Your browser can't decode this file. Convert it to MP4 (H.264/AAC) and try again.";

const abortErr = () => new DOMException("Cancelled", "AbortError");
const isVideo = (f) => f.type.startsWith("video") || /\.(mp4|mov|webm)$/i.test(f.name);

export function mediaDuration(file) {
  return new Promise((resolve, reject) => {
    const m = document.createElement(isVideo(file) ? "video" : "audio");
    const url = URL.createObjectURL(file);
    m.preload = "metadata";
    m.onloadedmetadata = () => { URL.revokeObjectURL(url); resolve(m.duration); };
    m.onerror = () => { URL.revokeObjectURL(url); reject(new Error(DECODE_HELP)); };
    m.src = url;
  });
}

export async function checkMedia(file) {
  if (file.size > LIMITS.bytes) throw new Error("This file is larger than 200 MB. Trim or compress it and try again.");
  const d = await mediaDuration(file);
  if (isFinite(d) && d > LIMITS.seconds) throw new Error("This recording is longer than 30 minutes. Split it and add the parts separately.");
  return isFinite(d) ? d : null;
}

export async function decodeTo16kMono(file, signal) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx || !window.OfflineAudioContext) throw new Error("This browser can't process audio. Try a recent Chrome, Edge, Firefox or Safari.");
  const ctx = new Ctx();
  let audio;
  try { audio = await ctx.decodeAudioData(await file.arrayBuffer()); } catch { throw new Error(DECODE_HELP); } finally { ctx.close(); }
  if (signal?.aborted) throw abortErr();
  const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(audio.duration * LIMITS.rate)), LIMITS.rate);
  const src = off.createBufferSource();
  src.buffer = audio;
  src.connect(off.destination);
  src.start();
  return (await off.startRendering()).getChannelData(0);
}

export function encodeWav(samples) {
  const rate = LIMITS.rate, buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

/** Split samples into <=100 s chunks, skipping near-silent ones (avoids speech-model hallucinations). */
export function audioChunks(samples) {
  const per = LIMITS.chunkS * LIMITS.rate, out = [];
  for (let i = 0; i * per < samples.length; i++) {
    const part = samples.subarray(i * per, Math.min(samples.length, (i + 1) * per));
    let sum = 0;
    for (let j = 0; j < part.length; j += 4) sum += part[j] * part[j];
    if (Math.sqrt(sum / Math.max(1, part.length / 4)) >= 0.003) out.push({ offset: i * LIMITS.chunkS, part });
  }
  return out;
}

function loadVideo(file) {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    Object.assign(v, { muted: true, preload: "auto", playsInline: true });
    v.onloadeddata = () => (v.videoWidth ? resolve(v) : reject(new Error(DECODE_HELP)));
    v.onerror = () => reject(new Error(DECODE_HELP));
    v.src = URL.createObjectURL(file);
  });
}

const seek = (video, t) => new Promise((resolve) => {
  const done = () => { clearTimeout(timer); video.removeEventListener("seeked", done); resolve(); };
  const timer = setTimeout(done, 4000);
  video.addEventListener("seeked", done);
  video.currentTime = t;
});
const toBlob = (canvas, q) => new Promise((r) => canvas.toBlob(r, "image/jpeg", q));

/** Keyframes every 3 s (max 40), near-identical frames skipped via a 32x32 pixel diff. */
export async function extractFrames(file, { signal, onProgress } = {}) {
  const video = await loadVideo(file);
  const scale = Math.min(1, 1280 / video.videoWidth);
  const mk = (w, h) => Object.assign(document.createElement("canvas"), { width: w, height: h });
  const canvas = mk(Math.round(video.videoWidth * scale), Math.round(video.videoHeight * scale));
  const thumbW = 240, thumb = mk(thumbW, Math.round(thumbW * video.videoHeight / video.videoWidth)), tiny = mk(32, 32);
  const ctx = canvas.getContext("2d"), tctx = tiny.getContext("2d", { willReadFrequently: true });
  const total = Math.min(LIMITS.maxFrames * 4, Math.floor(video.duration / LIMITS.frameEvery) + 1);
  const frames = [];
  let prev = null;
  try {
    for (let i = 0; i < total && frames.length < LIMITS.maxFrames; i++) {
      if (signal?.aborted) throw abortErr();
      const t = i * LIMITS.frameEvery;
      onProgress?.(i + 1, total, frames.length);
      await seek(video, t);
      tctx.drawImage(video, 0, 0, 32, 32);
      const px = tctx.getImageData(0, 0, 32, 32).data;
      if (prev) {
        let diff = 0;
        for (let j = 0; j < px.length; j += 4) diff += Math.abs(px[j] - prev[j]) + Math.abs(px[j + 1] - prev[j + 1]) + Math.abs(px[j + 2] - prev[j + 2]);
        if (diff / 3072 < 3) continue;
      }
      prev = px;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await toBlob(canvas, 0.92); // stays in the browser; read by OCR
      thumb.getContext("2d").drawImage(video, 0, 0, thumb.width, thumb.height);
      frames.push({ t, blob, thumb: thumb.toDataURL("image/jpeg", 0.6) });
    }
  } finally {
    URL.revokeObjectURL(video.src);
  }
  return frames;
}
