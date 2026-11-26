import assert from "node:assert/strict";
import test from "node:test";
import worker from "./index.js";

const SECRET = "test-worker-secret-token";
const MODEL = "@cf/meta/llama-3.1-8b-instruct-fast";

function makeReq(path, { method = "POST", headers = {}, body } = {}) {
  const h = new Headers(headers);
  return new Request(`https://viora-ai.reddyanvesh2008.workers.dev${path}`, { method, headers: h, body });
}

test("GET /health never invokes AI and returns 503 when unconfigured, 200 when configured", async () => {
  let called = 0;
  const ai = { run: async () => (++called, {}) };
  const r503 = await worker.fetch(makeReq("/health", { method: "GET" }), {});
  assert.equal(r503.status, 503);
  assert.equal((await r503.json()).inference, false);

  const r200 = await worker.fetch(makeReq("/health", { method: "GET" }), { API_KEY: SECRET, AI: ai });
  assert.equal(r200.status, 200);
  const body = await r200.json();
  assert.deepEqual(body, { status: "ok", service: "viora-inference", inference: false });
  assert.equal(r200.headers.get("Cache-Control"), "no-store");
  assert.equal(called, 0);
});

test("rejects missing/invalid auth, oversized stream, retired/unsupported model, bad messages, and token caps without invoking AI", async () => {
  let called = 0;
  const env = { API_KEY: SECRET, AI: { run: async () => (++called, { response: "{}" }) } };

  const unauth = await worker.fetch(
    makeReq("/v1/chat/completions", { headers: { "Content-Type": "application/json" }, body: "{}" }),
    env
  );
  assert.equal(unauth.status, 401);

  const huge = new ReadableStream({
    start(c) {
      c.enqueue(new Uint8Array(9000));
      c.enqueue(new Uint8Array(9000));
      c.close();
    },
  });
  const big = await worker.fetch(
    new Request("https://viora-ai.reddyanvesh2008.workers.dev/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: `Bearer ${SECRET}`, "Content-Type": "application/json" },
      body: huge,
      duplex: "half",
    }),
    env
  );
  assert.equal(big.status, 413);

  for (const payload of [
    { model: "@cf/meta/llama-3.2-3b-instruct", messages: [{ role: "user", content: "hi" }] },
    { model: MODEL, messages: [{ role: "tool", content: "hi" }] },
    { model: MODEL, messages: [{ role: "user", content: "hi" }], max_tokens: 501 },
    { model: MODEL, messages: [{ role: "user", content: "hi" }], max_tokens: 100, max_completion_tokens: 200 },
    { model: MODEL, messages: [{ role: "user", content: "hi" }], stream: true },
  ]) {
    const bad = await worker.fetch(
      makeReq("/v1/chat/completions", {
        headers: { Authorization: `Bearer ${SECRET}`, "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
      env
    );
    assert.equal(bad.status, 422);
  }
  assert.equal(called, 0);
});

test("translates OpenAI json_schema, invokes env.AI.run once, preserves usage/omits missing usage", async () => {
  const seen = [];
  const envWithUsage = {
    API_KEY: SECRET,
    AI: {
      run: async (model, input) => {
        seen.push({ model, input });
        return { response: '{"concepts":["Velocity"]}', usage: { prompt_tokens: 40, completion_tokens: 12, total_tokens: 52 } };
      },
    },
  };
  const r1 = await worker.fetch(
    makeReq("/v1/chat/completions", {
      headers: { Authorization: `Bearer ${SECRET}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: MODEL,
        messages: [{ role: "user", content: "Extract concepts" }],
        max_tokens: 300,
        response_format: { type: "json_schema", json_schema: { name: "Concepts", strict: true, schema: { type: "object" } } },
      }),
    }),
    envWithUsage
  );
  assert.equal(r1.status, 200);
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].input.response_format, { type: "json_schema", json_schema: { type: "object" } });
  const j1 = await r1.json();
  assert.equal(j1.choices[0].message.content, '{"concepts":["Velocity"]}');
  assert.equal(j1.usage.total_tokens, 52);
  assert.equal("finish_reason" in j1.choices[0], false);

  // Missing usage metadata is NOT fabricated
  const envNoUsage = { API_KEY: SECRET, AI: { run: async () => ({ response: '{"ok":true}' }) } };
  const r2 = await worker.fetch(
    makeReq("/v1/chat/completions", {
      headers: { Authorization: `Bearer ${SECRET}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: MODEL, messages: [{ role: "user", content: "hi" }], response_format: { type: "json_object" } }),
    }),
    envNoUsage
  );
  assert.equal(r2.status, 200);
  const j2 = await r2.json();
  assert.equal("usage" in j2, false);
});

test("maps malformed JSON outputs to 502 and quota exhaustion to 429 AI_QUOTA_EXCEEDED", async () => {
  const badEnv = { API_KEY: SECRET, AI: { run: async () => ({ response: "not-json" }) } };
  const rBad = await worker.fetch(
    makeReq("/v1/chat/completions", {
      headers: { Authorization: `Bearer ${SECRET}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: MODEL, messages: [{ role: "user", content: "hi" }], response_format: { type: "json_object" } }),
    }),
    badEnv
  );
  assert.equal(rBad.status, 502);

  const quotaEnv = {
    API_KEY: SECRET,
    AI: { run: async () => { throw new Error("You have used up your daily free allocation of 10,000 neurons (3036)"); } },
  };
  const rQuota = await worker.fetch(
    makeReq("/v1/chat/completions", {
      headers: { Authorization: `Bearer ${SECRET}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: MODEL, messages: [{ role: "user", content: "hi" }] }),
    }),
    quotaEnv
  );
  assert.equal(rQuota.status, 429);
  assert.equal((await rQuota.json()).error.code, "AI_QUOTA_EXCEEDED");
});
