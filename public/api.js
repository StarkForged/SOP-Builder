// Thin fetch wrapper with friendly, actionable error messages.
export class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

const FRIENDLY = {
  daily_free_limit: "Free AI limit reached, try again in a minute.",
  rate_limited: "Too many requests from this browser. Wait a few minutes and try again.",
  consent_required: "Tick the consent box before uploading audio or video.",
  provider_error: "The AI service didn't respond. Please try again in a moment.",
  source_too_long: "Your sources are too long for the free AI tier. Remove or shorten a source and try again.",
  generation_failed: "The AI returned an unusable draft. Please try again.",
};

export async function request(path, { json, form, signal, method } = {}) {
  const opts = { method: method || (json || form ? "POST" : "GET"), signal };
  if (json) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(json); }
  if (form) {
    const fd = new FormData();
    for (const [k, v] of Object.entries(form)) {
      for (const item of [].concat(v)) {
        if (item && item.blob) fd.append(k, item.blob, item.name); else fd.append(k, item);
      }
    }
    opts.body = fd;
  }
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(0, "network", "Couldn't reach the server. Check your connection and try again.");
  }
  if (resp.ok) return resp.headers.get("content-type")?.includes("json") ? resp.json() : resp;
  let data = {};
  try { data = await resp.json(); } catch { /* not JSON */ }
  throw new ApiError(resp.status, data.error || "http_" + resp.status, FRIENDLY[data.error] || data.message || `Something went wrong (error ${resp.status}). Please try again.`);
}
