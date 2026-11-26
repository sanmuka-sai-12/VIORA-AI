const ALLOWED_MODEL = "@cf/meta/llama-3.1-8b-instruct-fast";
const RETIRED_OR_UNSUPPORTED_PREFIXES = ["@cf/meta/llama-3.2-"];
const MAX_BODY_BYTES = 16 * 1024;
const MAX_MESSAGES = 16;
const MAX_OUTPUT_TOKENS = 500;
const MAX_RESPONSE_CHARS = 65_536;
const ALLOWED_ROLES = new Set(["system", "user", "assistant"]);
const ALLOWED_FIELDS = new Set([
  "model",
  "messages",
  "temperature",
  "max_tokens",
  "max_completion_tokens",
  "response_format",
  "stream",
]);
const QUOTA_MARKERS = [
  "daily free allocation",
  "neurons",
  "3036",
  "4006",
  "insufficient_quota",
  "quota exceeded",
  "ai_quota_exceeded",
];

function correlationId(request) {
  const hdr = request.headers.get("X-Request-ID");
  if (hdr && /^[a-zA-Z0-9._-]{1,64}$/.test(hdr)) return hdr;
  return crypto.randomUUID();
}

function jsonResp(status, body, reqId) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Request-ID": reqId,
    },
  });
}

function errResp(status, code, message, type, reqId) {
  return jsonResp(status, { error: { code, message, type }, request_id: reqId }, reqId);
}

async function constantTimeSecretMatch(authHeader, expectedSecret) {
  if (typeof authHeader !== "string" || !authHeader.startsWith("Bearer ")) return false;
  const token = authHeader.slice(7).trim();
  if (!token || typeof expectedSecret !== "string" || !expectedSecret) return false;
  const enc = new TextEncoder();
  const [aBuf, bBuf] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(token)),
    crypto.subtle.digest("SHA-256", enc.encode(expectedSecret)),
  ]);
  const a = new Uint8Array(aBuf);
  const b = new Uint8Array(bBuf);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

async function readBoundedJson(request) {
  if (!request.body) return { ok: false, status: 422, code: "empty_body", message: "Request body required" };
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
      await reader.cancel().catch(() => {});
      return { ok: false, status: 413, code: "payload_too_large", message: "Request body exceeds 16 KB limit" };
    }
    chunks.push(value);
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    merged.set(c, offset);
    offset += c.byteLength;
  }
  try {
    const parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(merged));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, status: 422, code: "invalid_json", message: "JSON object payload required" };
    }
    return { ok: true, data: parsed };
  } catch {
    return { ok: false, status: 422, code: "invalid_json", message: "Malformed JSON payload" };
  }
}

function validateAndTranslatePayload(body) {
  for (const key of Object.keys(body)) {
    if (!ALLOWED_FIELDS.has(key)) {
      return { error: [422, "unsupported_parameter", `Unsupported parameter: ${key}`] };
    }
  }
  if (typeof body.model !== "string" || body.model.trim() !== ALLOWED_MODEL) {
    const m = typeof body.model === "string" ? body.model.trim() : "";
    const retired = RETIRED_OR_UNSUPPORTED_PREFIXES.some((p) => m.startsWith(p));
    return {
      error: [
        422,
        retired ? "model_retired" : "unsupported_model",
        retired ? "Requested model is retired; use configured active model" : "Only the configured free-tier model is permitted",
      ],
    };
  }
  if (body.stream !== undefined && body.stream !== false) {
    return { error: [422, "streaming_unsupported", "Streaming is not supported; set stream=false"] };
  }
  if (!Array.isArray(body.messages) || body.messages.length === 0 || body.messages.length > MAX_MESSAGES) {
    return { error: [422, "invalid_messages", `messages must contain 1..${MAX_MESSAGES} entries`] };
  }
  const messages = [];
  for (const msg of body.messages) {
    if (!msg || typeof msg !== "object" || !ALLOWED_ROLES.has(msg.role) || typeof msg.content !== "string" || !msg.content) {
      return { error: [422, "invalid_messages", "Each message requires valid role and non-empty string content"] };
    }
    messages.push({ role: msg.role, content: msg.content });
  }

  const hasMaxTok = body.max_tokens !== undefined;
  const hasMaxComp = body.max_completion_tokens !== undefined;
  if (hasMaxTok && hasMaxComp && body.max_tokens !== body.max_completion_tokens) {
    return { error: [422, "ambiguous_token_limit", "Conflicting max_tokens and max_completion_tokens"] };
  }
  const rawMax = hasMaxComp ? body.max_completion_tokens : body.max_tokens;
  let maxTokens = MAX_OUTPUT_TOKENS;
  if (rawMax !== undefined) {
    if (!Number.isInteger(rawMax) || rawMax < 1 || rawMax > MAX_OUTPUT_TOKENS) {
      return { error: [422, "output_token_limit_exceeded", `Output token limit must be an integer between 1 and ${MAX_OUTPUT_TOKENS}`] };
    }
    maxTokens = rawMax;
  }

  let temperature;
  if (body.temperature !== undefined) {
    if (typeof body.temperature !== "number" || !Number.isFinite(body.temperature) || body.temperature < 0 || body.temperature > 2) {
      return { error: [422, "invalid_temperature", "temperature must be between 0 and 2"] };
    }
    temperature = body.temperature;
  }

  let responseFormat;
  if (body.response_format !== undefined) {
    const rf = body.response_format;
    if (!rf || typeof rf !== "object" || Array.isArray(rf)) {
      return { error: [422, "invalid_response_format", "response_format must be an object"] };
    }
    if (rf.type === "json_object") {
      responseFormat = { type: "json_object" };
    } else if (rf.type === "json_schema" && rf.json_schema && typeof rf.json_schema === "object") {
      const schemaObj = rf.json_schema.schema && typeof rf.json_schema.schema === "object" ? rf.json_schema.schema : rf.json_schema;
      responseFormat = { type: "json_schema", json_schema: schemaObj };
    } else {
      return { error: [422, "unsupported_response_format", "Only json_object and json_schema response_format are supported"] };
    }
  }

  const aiInput = { messages, max_tokens: maxTokens };
  if (temperature !== undefined) aiInput.temperature = temperature;
  if (responseFormat !== undefined) aiInput.response_format = responseFormat;
  return { aiInput, structuredRequested: Boolean(responseFormat) };
}

function classifyProviderFailure(err) {
  const raw = String(err?.message || err || "").toLowerCase();
  const code = String(err?.code || "").toLowerCase();
  const combined = `${raw} ${code}`;
  if (QUOTA_MARKERS.some((m) => combined.includes(m))) {
    return [429, "AI_QUOTA_EXCEEDED", "Daily free allocation exhausted", "quota_exceeded"];
  }
  if (combined.includes("429") || combined.includes("rate limit") || combined.includes("too many requests") || combined.includes("capacity")) {
    return [429, "AI_RATE_LIMITED", "AI provider capacity throttled", "rate_limit_exceeded"];
  }
  if (combined.includes("5028") || combined.includes("gone") || combined.includes("retired") || combined.includes("unavailable")) {
    return [422, "AI_MODEL_UNAVAILABLE", "Configured AI model unavailable", "invalid_request_error"];
  }
  return [502, "AI_PROVIDER_ERROR", "Upstream AI inference failed", "provider_error"];
}

function normalizeAiOutput(raw, structuredRequested) {
  if (!raw || typeof raw !== "object") return null;
  let content = null;
  let finishReason;
  if (typeof raw.response === "string") {
    content = raw.response;
  } else if (raw.response && typeof raw.response === "object") {
    content = JSON.stringify(raw.response);
  } else if (Array.isArray(raw.choices) && raw.choices[0]?.message && typeof raw.choices[0].message.content === "string") {
    content = raw.choices[0].message.content;
    if (typeof raw.choices[0].finish_reason === "string") finishReason = raw.choices[0].finish_reason;
  }
  if (typeof raw.finish_reason === "string" && !finishReason) finishReason = raw.finish_reason;
  if (typeof content !== "string" || content.length === 0 || content.length > MAX_RESPONSE_CHARS) return null;

  if (structuredRequested) {
    try {
      JSON.parse(content);
    } catch {
      return null;
    }
  }

  let usage;
  if (raw.usage && typeof raw.usage === "object") {
    const pt = raw.usage.prompt_tokens;
    const ct = raw.usage.completion_tokens;
    const tt = raw.usage.total_tokens;
    if (Number.isInteger(pt) && pt >= 0 && Number.isInteger(ct) && ct >= 0) {
      usage = { prompt_tokens: pt, completion_tokens: ct, total_tokens: Number.isInteger(tt) && tt >= 0 ? tt : pt + ct };
    }
  }
  return { content, finishReason, usage };
}

export default {
  async fetch(request, env) {
    const reqId = correlationId(request);
    const url = new URL(request.url);
    const configured = Boolean(env?.API_KEY && typeof env.API_KEY === "string" && env.API_KEY.trim() && env?.AI && typeof env.AI.run === "function");

    if (request.method === "GET" && url.pathname === "/health") {
      return jsonResp(
        configured ? 200 : 503,
        { status: configured ? "ok" : "not_configured", service: "viora-inference", inference: false },
        reqId
      );
    }

    if (request.method !== "POST" || url.pathname !== "/v1/chat/completions") {
      return errResp(404, "not_found", "Endpoint not found", "invalid_request_error", reqId);
    }
    if (!configured) {
      return errResp(503, "not_configured", "Worker inference binding or secret is not configured", "configuration_error", reqId);
    }
    if (!(await constantTimeSecretMatch(request.headers.get("Authorization") || "", env.API_KEY))) {
      return errResp(401, "unauthorized", "Invalid or missing bearer credentials", "authentication_error", reqId);
    }
    const ctype = (request.headers.get("Content-Type") || "").toLowerCase();
    if (!ctype.startsWith("application/json")) {
      return errResp(422, "unsupported_media_type", "Content-Type must be application/json", "invalid_request_error", reqId);
    }

    const bodyRes = await readBoundedJson(request);
    if (!bodyRes.ok) {
      return errResp(bodyRes.status, bodyRes.code, bodyRes.message, "invalid_request_error", reqId);
    }
    const translated = validateAndTranslatePayload(bodyRes.data);
    if (translated.error) {
      const [status, code, message] = translated.error;
      return errResp(status, code, message, "invalid_request_error", reqId);
    }

    const t0 = Date.now();
    let rawOut;
    try {
      rawOut = await env.AI.run(ALLOWED_MODEL, translated.aiInput);
    } catch (err) {
      const [status, code, message, type] = classifyProviderFailure(err);
      return errResp(status, code, message, type, reqId);
    }
    const latencyMs = Math.max(0, Date.now() - t0);

    const normalized = normalizeAiOutput(rawOut, translated.structuredRequested);
    if (!normalized) {
      return errResp(502, "AI_INVALID_PROVIDER_RESPONSE", "Upstream AI returned invalid or malformed response", "provider_error", reqId);
    }

    const choice = { index: 0, message: { role: "assistant", content: normalized.content } };
    if (normalized.finishReason) choice.finish_reason = normalized.finishReason;

    const out = {
      id: `chatcmpl-${reqId}`,
      object: "chat.completion",
      created: Math.floor(Date.now() / 1000),
      model: ALLOWED_MODEL,
      choices: [choice],
      request_id: reqId,
      latency_ms: latencyMs,
    };
    if (normalized.usage) out.usage = normalized.usage;
    return jsonResp(200, out, reqId);
  },
};
