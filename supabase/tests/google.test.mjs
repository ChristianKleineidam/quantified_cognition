import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createHandler as realHandler, validatePackage } from "../functions/qs-service/service.mjs";
const createHandler = options => realHandler({ ...options,
  operations: { admit: async () => {}, record: async () => {} } });
import schema from "../functions/qs-service/schema.json" with { type: "json" };

const env = { GOOGLE_HEALTH_ENABLED: "true", OURA_ENABLED: "true",
  GOOGLE_CLIENT_ID: "synthetic-google-client", GOOGLE_CLIENT_SECRET: "server-only-secret",
  OURA_CLIENT_ID: "synthetic-oura", OURA_CLIENT_SECRET: "oura-server-only",
  SERVICE_URL: "https://example.supabase.co/functions/v1/qs-service", OAUTH_STATE_SECRET: "s".repeat(48) };
const scope = "https://www.googleapis.com/auth/googlehealth.sleep.readonly";
const verifier = "v".repeat(64), code_challenge = createHash("sha256").update(verifier).digest("base64url");
const start = { return_uri: "http://127.0.0.1:12345/" + "p".repeat(43), state: "x".repeat(43), code_challenge };
const request = (route, data) => new Request(env.SERVICE_URL + route, { method: "POST",
  headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

test("Google OAuth is scope-limited, PKCE-bound and isolated from Oura flows", async () => {
  const calls = [];
  const handler = createHandler({ env, fetch: async (url, options) => {
    calls.push([url, options]);
    return Response.json({ access_token: "access", refresh_token: "refresh", expires_in: 3600, scope });
  } });
  const begun = await (await handler(request("/google/oauth/start", start))).json();
  const url = new URL(begun.authorize_url);
  assert.equal(url.origin + url.pathname, "https://accounts.google.com/o/oauth2/v2/auth");
  assert.equal(url.searchParams.get("redirect_uri"), env.SERVICE_URL + "/google/oauth/callback");
  assert.equal(url.searchParams.get("access_type"), "offline");
  assert.equal(url.searchParams.get("prompt"), "consent");
  assert.equal(url.searchParams.get("code_challenge"), code_challenge);
  assert.deepEqual(new Set(url.searchParams.get("scope").split(" ")), new Set([scope,
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"]));
  assert.ok(!begun.authorize_url.includes(env.GOOGLE_CLIENT_SECRET));
  const returned = await handler(new Request(env.SERVICE_URL + "/google/oauth/callback?code=one-use&state=" + encodeURIComponent(begun.flow)));
  const target = new URL(returned.headers.get("location"));
  assert.equal(returned.status, 303);
  assert.equal(target.origin + target.pathname, start.return_uri);
  assert.equal(target.searchParams.get("state"), start.state);
  assert.equal(target.searchParams.get("code"), "one-use");
  const exchange = { code: "one-use", code_verifier: verifier, flow: begun.flow };
  const tokens = await handler(request("/google/oauth/exchange", exchange));
  assert.equal(tokens.status, 200);
  assert.equal((await tokens.json()).scope, scope); // One granted scope is valid.
  assert.equal(calls[0][0], "https://oauth2.googleapis.com/token");
  assert.equal(calls[0][1].body.get("client_secret"), env.GOOGLE_CLIENT_SECRET);
  assert.equal(calls[0][1].body.get("code_verifier"), verifier);
  assert.equal(calls[0][1].redirect, "error");
  assert.equal((await handler(request("/google/oauth/exchange", { ...exchange, code_verifier: "w".repeat(64) }))).status, 400);
  assert.equal((await handler(request("/oauth/exchange", exchange))).status, 400);
  assert.equal((await handler(new Request(env.SERVICE_URL + "/oauth/callback?state=" + encodeURIComponent(begun.flow)))).status, 400);
  const oura = await (await handler(request("/oauth/start", start))).json();
  assert.equal((await handler(request("/google/oauth/exchange", { ...exchange, flow: oura.flow }))).status, 400);
  const tampered = begun.flow.slice(0, -3) + "xyz";
  assert.equal((await handler(request("/google/oauth/exchange", { ...exchange, flow: tampered }))).status, 400);
  const denied = await handler(new Request(env.SERVICE_URL + "/google/oauth/callback?error=denied&state=" + encodeURIComponent(begun.flow)));
  assert.equal(new URL(denied.headers.get("location")).searchParams.get("error"), "access_denied");
  assert.equal(calls.length, 1);
});

test("Google refresh can omit refresh token and scopes; revocation and errors never expose secrets", async () => {
  const handler = createHandler({ env, fetch: async (url, options) => {
    if (url.endsWith("/revoke")) {
      assert.equal(options.body.get("token"), "refresh");
      return new Response("");
    }
    assert.equal(options.body.get("refresh_token"), "refresh");
    return Response.json({ access_token: "renewed", expires_in: 3600 });
  } });
  const refreshed = await handler(request("/google/oauth/refresh", { refresh_token: "refresh" }));
  assert.equal(refreshed.status, 200);
  assert.deepEqual(await refreshed.json(), { access_token: "renewed", expires_in: 3600 });
  assert.equal((await handler(request("/google/oauth/revoke", { token: "refresh" }))).status, 200);
  const bad = createHandler({ env, fetch: async () => new Response("private-google-token", { status: 400 }) });
  const denied = await bad(request("/google/oauth/refresh", { refresh_token: "refresh" }));
  assert.equal(denied.status, 401);
  assert.ok(!(await denied.text()).includes("private-google-token"));
});

test("Google disabled is independent and cannot contact Google", async () => {
  const handler = createHandler({ env: { ...env, GOOGLE_HEALTH_ENABLED: "false" }, fetch: () => assert.fail("Upstream call") });
  const status = await (await handler(new Request(env.SERVICE_URL + "/status"))).json();
  assert.equal(status.google_health_enabled, false);
  assert.equal(status.oura_enabled, true);
  for (const path of ["start", "exchange", "refresh", "revoke"]) {
    assert.equal((await handler(request("/google/oauth/" + path, {}))).status, 503);
  }
  assert.equal((await handler(new Request(env.SERVICE_URL + "/google/oauth/callback"))).status, 503);
});

test("Google daily fields are allowlisted and research contract changes reject old packages", () => {
  const metadata = Object.fromEntries(schema.metadata.map(k => [k, ""]));
  Object.assign(metadata, { research_participant_id: crypto.randomUUID(), research_consent_active: true,
    sharing_current_choice: "opt_in", sharing_notice_version: "v3", sharing_notice_hash: "a".repeat(64) });
  const packet = { schema_version: 5, enrollment_id: crypto.randomUUID(), submission_id: crypto.randomUUID(), created_at: new Date().toISOString(),
    metadata, columns: schema.columns, rows: [schema.columns.map(name => ({ date: "2026-09-07",
      google_health_sleep_status: "durations available", google_health_sleep_types: "STAGES",
      google_health_day_basis: "sleep_end_local_date_steps_civil_date", google_health_steps: "0",
      google_health_total_sleep_seconds: "25200" })[name] || "")] };
  const notice = { RESEARCH_NOTICE_VERSION: "v3", RESEARCH_NOTICE_HASH: "a".repeat(64) };
  validatePackage(packet, notice);
  assert.throws(() => validatePackage({ ...packet, schema_version: 1 }, notice));
  packet.rows[0][schema.columns.indexOf("google_health_sleep_types")] = "users/private-account";
  assert.throws(() => validatePackage(packet, notice));
});
