import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createHandler as realHandler, sha256, validatePackage } from "../functions/qs-service/service.mjs";
// Provider/schema unit tests isolate operations; operations.test.mjs exercises production admission.
const createHandler = options => realHandler({ ...options,
  operations: { admit: async () => {}, record: async () => {} } });
import schema from "../functions/qs-service/schema.json" with { type: "json" };
import { envelope, reviews } from "./encrypted-fixtures.mjs";

const env = {
  OURA_ENABLED: "true", RESEARCH_ENABLED: "true", OAUTH_STATE_SECRET: "s".repeat(48),
  OURA_CLIENT_ID: "synthetic-client", OURA_CLIENT_SECRET: "synthetic-server-secret",
  SERVICE_URL: "https://example.supabase.co/functions/v1/qs-service",
  SUPABASE_URL: "https://example.supabase.co", SUPABASE_SERVICE_ROLE_KEY: "server-only",
  RESEARCH_OPERATOR: "Synthetic study", PRIVACY_URL: "https://example.org/privacy",
  RESEARCH_NOTICE_VERSION: "test-v1", RESEARCH_NOTICE_HASH: "a".repeat(64)
};
const id = "00000000-0000-4000-8000-000000000001", secret = "c".repeat(43);
const auth = { authorization: "Bearer " + id + ":" + secret };
function request(route, value, headers = {}, method = "POST") {
  return new Request(env.SERVICE_URL + route, { method,
    headers: { "content-type": "application/json", ...headers },
    ...(value !== undefined ? { body: JSON.stringify(value) } : {}) });
}
const response = value => new Response(JSON.stringify(value));
function packet() {
  const metadata = Object.fromEntries(schema.metadata.map(k => [k, ""]));
  Object.assign(metadata, { research_participant_id: id, research_consent_active: true,
    sharing_current_choice: "opt_in", sharing_notice_version: env.RESEARCH_NOTICE_VERSION,
    sharing_notice_hash: env.RESEARCH_NOTICE_HASH });
  return { schema_version: schema.package_version, enrollment_id: id, submission_id: crypto.randomUUID(), created_at: new Date().toISOString(),
    metadata, columns: schema.columns, reviews: reviews(),
    rows: [schema.columns.map(k => k === "date" ? "2026-10-01" : "")] };
}

test("OAuth code + PKCE, exact redirect, state integrity and denial", async () => {
  const calls = [];
  const handler = createHandler({ env, now: () => 1000000, fetch: async (url, options) => {
    calls.push([url, options]); return response({ access_token: "access", refresh_token: "refresh", expires_in: 3600 });
  } });
  const verifier = "v".repeat(64);
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const return_uri = "http://127.0.0.1:12345/" + "p".repeat(43);
  const started = await handler(request("/oauth/start", { return_uri, state: "x".repeat(43), code_challenge: challenge }));
  assert.equal(started.status, 200);
  const start = await started.json(), authorize = new URL(start.authorize_url);
  assert.equal(authorize.origin, "https://cloud.ouraring.com");
  assert.equal(authorize.searchParams.get("scope"), "daily workout");
  assert.equal(authorize.searchParams.get("code_challenge_method"), "S256");
  assert.equal(authorize.searchParams.get("redirect_uri"), env.SERVICE_URL + "/oauth/callback");
  assert.ok(!start.authorize_url.includes(env.OURA_CLIENT_SECRET));
  const callback = await handler(new Request(env.SERVICE_URL + "/oauth/callback?state=" + encodeURIComponent(start.flow) + "&code=one-use"));
  assert.equal(callback.status, 303);
  const redirect = new URL(callback.headers.get("location"));
  assert.equal(redirect.origin + redirect.pathname, return_uri);
  assert.equal(redirect.searchParams.get("state"), "x".repeat(43));
  assert.equal(redirect.searchParams.get("code"), "one-use");
  const denied = await handler(new Request(env.SERVICE_URL + "/oauth/callback?state=" + encodeURIComponent(start.flow) + "&error=denied"));
  assert.equal(new URL(denied.headers.get("location")).searchParams.get("error"), "access_denied");
  const valid = await handler(request("/oauth/exchange", { code: "one-use", code_verifier: verifier, flow: start.flow }));
  assert.equal(valid.status, 200);
  assert.equal(calls[0][0], "https://api.ouraring.com/oauth/token");
  assert.equal(calls[0][1].body.get("client_secret"), env.OURA_CLIENT_SECRET);
  assert.equal(calls[0][1].redirect, "error");
  assert.equal(calls[0][1].body.get("code_verifier"), verifier);
  assert.equal(valid.headers.get("cache-control"), "no-store");
  assert.equal((await handler(request("/oauth/exchange", { code: "one-use", code_verifier: "wrong".repeat(12), flow: start.flow }))).status, 400);
  const pieces = start.flow.split("."); pieces[1] = "b" + pieces[1].slice(1);
  assert.equal((await handler(new Request(env.SERVICE_URL + "/oauth/callback?state=" + pieces.join(".")))).status, 400);
  const expired = createHandler({ env, now: () => 2000000, fetch: () => assert.fail() });
  assert.equal((await expired(request("/oauth/exchange", { code: "one-use", code_verifier: verifier, flow: start.flow }))).status, 400);
  for (const uri of ["https://evil.test/" + "p".repeat(43), "http://localhost:12345/" + "p".repeat(43), return_uri + "?forward=evil", "http://127.0.0.1.evil.test:12345/" + "p".repeat(43)]) {
    assert.equal((await handler(request("/oauth/start", { return_uri: uri, state: "x".repeat(43), code_challenge: challenge }))).status, 400);
  }
});

test("refresh rotates tokens, provider errors are sanitized, disabled features stay off", async () => {
  let calls = 0;
  const handler = createHandler({ env, fetch: async (url, options) => {
    calls++; assert.equal(options.body.get("grant_type"), "refresh_token");
    assert.equal(options.body.get("refresh_token"), "old-refresh");
    return response({ access_token: "new-access", refresh_token: "new-refresh", expires_in: 7200 });
  } });
  const result = await handler(request("/oauth/refresh", { refresh_token: "old-refresh" }));
  assert.equal((await result.json()).refresh_token, "new-refresh"); assert.equal(calls, 1);
  const broken = createHandler({ env, fetch: async () => new Response("private-token", { status: 401 }) });
  const error = await broken(request("/oauth/refresh", { refresh_token: "old-refresh" }));
  assert.equal(error.status, 401); assert.ok(!(await error.text()).includes("private-token"));
  const off = createHandler({ env: {}, fetch: () => assert.fail() });
  assert.equal((await off(request("/oauth/refresh", { refresh_token: "old-refresh" }))).status, 503);
});

test("strict aggregate schema refuses raw records, missing consent, bad columns and non-finite numbers", () => {
  validatePackage(packet(), env);
  for (const mutate of [p => { p.cards = []; }, p => { p.metadata.research_consent_active = false; },
    p => { p.metadata.sharing_notice_hash = "old"; }, p => { p.columns = [...p.columns, "card_text"]; },
    p => { p.rows[0][schema.columns.indexOf("recall_score_pp")] = "NaN"; },
    p => { p.rows.push(p.rows[0]); }, p => { p.metadata.access_token = "secret"; }]) {
    const value = packet(); mutate(value); assert.throws(() => validatePackage(value, env));
  }
});

test("review snapshots allow pseudonymous predictions and reject raw identifiers or invalid fields", () => {
  const value = packet();
  value.reviews.rows = [["P".repeat(22), 1, value.rows[0][0], 3, 1, 0.8,
    86400, 1, 0, true, true, true, false]];
  validatePackage(value, env);
  for (const [name, invalid] of [["card_id", "1234567890123"], ["ordinal", 2],
    ["prediction", NaN], ["prediction", 1.1], ["success", 0],
    ["elapsed_seconds", -1], ["last_on_day", 1]]) {
    const bad = structuredClone(value);
    bad.reviews.rows[0][schema.review_columns.indexOf(name)] = invalid;
    assert.throws(() => validatePackage(bad, env));
  }
  const missing = structuredClone(value);
  missing.reviews.rows[0][schema.review_columns.indexOf("prediction")] = null;
  validatePackage(missing, env);
  value.reviews.context.original_card_id = 1234567890123;
  assert.throws(() => validatePackage(value, env));
});

test("private research requests authenticate a separate capability; deletion works with uploads off", async () => {
  const calls = [];
  const handler = createHandler({ env, fetch: async (url, options) => {
    calls.push([url, JSON.parse(options.body)]);
    assert.equal(options.headers.apikey, "server-only");
    return response(url.endsWith("qs_submit") ? { submission_id: calls.at(-1)[1].p_submission, received_at: "now" } : true);
  } });
  assert.equal((await handler(request("/research/participant", {}, auth))).status, 200);
  const value = envelope(packet());
  assert.equal((await handler(request("/research/submissions", value, auth))).status, 200);
  assert.equal(calls[1][1].p_hash, await sha256(secret));
  assert.equal(calls[1][1].p_payload.metadata.research_participant_id, id);
  assert.equal(calls[1][1].p_payload_hash, await sha256(JSON.stringify(value)));
  assert.ok(!JSON.stringify(calls).includes(secret));
  assert.equal((await handler(request("/research/submissions", value))).status, 401);
  assert.equal(calls.length, 2);
  const disabled = createHandler({ env: { ...env, RESEARCH_ENABLED: "false" }, fetch: async () => response(true) });
  assert.equal((await disabled(request("/research/participant", {receipt_secret:secret}, auth, "DELETE"))).status, 200);
  assert.equal((await disabled(request("/research/submissions", value, auth))).status, 503);
});

export { env, packet, auth, request };

test("prelaunch deployment refuses every Oura and research write without upstream calls", async () => {
  const disabled = createHandler({ env: { ...env, OURA_ENABLED: "false", RESEARCH_ENABLED: "false" },
    fetch: () => assert.fail("Disabled route contacted Oura or the database") });
  const state = await disabled(new Request(env.SERVICE_URL + "/status"));
  assert.equal(state.status, 200);
  assert.equal((await state.json()).oura_enabled, false);
  const status = await (await disabled(new Request(env.SERVICE_URL + "/status"))).json();
  assert.equal(status.research_enabled, false);
  for (const route of ["/oauth/start", "/oauth/exchange", "/oauth/refresh", "/oauth/revoke",
                       "/research/participant", "/research/submissions"]) {
    assert.equal((await disabled(request(route, packet(), auth))).status, 503, route);
  }
  assert.equal((await disabled(new Request(env.SERVICE_URL + "/oauth/callback?code=synthetic"))).status, 503);
});

test("hosted modern server keys take precedence and never become Bearer JWTs", async () => {
  const handler = createHandler({ env: { ...env, SUPABASE_SECRET_KEYS: JSON.stringify({ default: "sb_secret_synthetic" }) },
    fetch: async (_url, options) => {
      assert.equal(options.headers.apikey, "sb_secret_synthetic");
      assert.equal(options.headers.Authorization, undefined);
      return response(true);
    } });
  assert.equal((await handler(request("/research/participant", {}, auth))).status, 200);
});
