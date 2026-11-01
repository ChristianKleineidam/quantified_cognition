import schema from "./schema.json" with { type: "json" };
import { googleOAuth } from "./google.mjs";
import { createOperations, routeLabel } from "./operations.mjs";

const encoder = new TextEncoder();
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SECRET = /^[A-Za-z0-9_-]{43,128}$/;
const B64 = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes)))
  .replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
const decode = text => Uint8Array.from(atob(text.replaceAll("-", "+").replaceAll("_", "/")), c => c.charCodeAt(0));
export const sha256 = async value => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value))))
  .map(v => v.toString(16).padStart(2, "0")).join("");
class Failure extends Error { constructor(status, retryAfter) { super("Request failed"); this.status = status; this.retryAfter = retryAfter; } }
const fail = (status, retryAfter) => { throw new Failure(status, retryAfter); };
const json = (value, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" }
});
function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).some(k => !keys.includes(k)) || keys.some(k => !(k in value))) fail(400);
}
async function body(request, limit = 16384) {
  if (!request.headers.get("content-type")?.startsWith("application/json")) fail(415);
  const reader = request.body?.getReader();
  if (!reader) fail(400);
  let size = 0; const chunks = [];
  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.length; if (size > limit) { await reader.cancel(); fail(413); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size); let pos = 0;
    for (const chunk of chunks) { bytes.set(chunk, pos); pos += chunk.length; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch (error) { if (error instanceof Failure) throw error; fail(400); }
}
function loopback(value) {
  let url; try { url = new URL(value); } catch { fail(400); }
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port ||
      url.username || url.password || url.search || url.hash || !/^\/[A-Za-z0-9_-]{32,128}$/.test(url.pathname)) fail(400);
  return url;
}
function credentials(request) {
  const auth = request.headers.get("authorization") || "";
  const [id, secret, extra] = auth.slice(7).split(":");
  if (!auth.startsWith("Bearer ") || !UUID.test(id) || !SECRET.test(secret) || extra) fail(401);
  return { id, secret };
}
export function validatePackage(value, env) {
  exact(value, ["schema_version", "submission_id", "created_at", "metadata", "columns", "rows"]);
  if (value.schema_version !== schema.package_version || !UUID.test(value.submission_id) ||
      typeof value.created_at !== "string" || !Number.isFinite(Date.parse(value.created_at))) fail(400);
  exact(value.metadata, schema.metadata);
  if (value.metadata.sharing_current_choice !== "opt_in" || value.metadata.research_consent_active !== true ||
      value.metadata.sharing_notice_version !== env.RESEARCH_NOTICE_VERSION ||
      value.metadata.sharing_notice_hash !== env.RESEARCH_NOTICE_HASH ||
      !UUID.test(value.metadata.research_participant_id)) fail(403);
  if (schema.metadata.some(k => !["string", "boolean"].includes(typeof value.metadata[k]) && value.metadata[k] !== null)) fail(400);
  if (schema.metadata.some(k => typeof value.metadata[k] === "string" && value.metadata[k].length > 256)) fail(400);
  if (JSON.stringify(value.columns) !== JSON.stringify(schema.columns) || !Array.isArray(value.rows) ||
      !value.rows.length || value.rows.length > 36600) fail(400);
  let previous = "";
  for (const row of value.rows) {
    if (!Array.isArray(row) || row.length !== schema.columns.length ||
        !/^\d{4}-\d{2}-\d{2}$/.test(row[0]) || row[0] <= previous ||
        !Number.isFinite(Date.parse(row[0] + "T00:00:00Z"))) fail(400);
    previous = row[0];
    for (let i = 0; i < row.length; i++) {
      const cell = row[i];
      if (typeof cell !== "string" || cell.length > 160) fail(400);
      const name = schema.columns[i];
      if (cell && schema.numeric.includes(name) && (!Number.isFinite(Number(cell)) || cell.trim() !== cell)) fail(400);
      if (schema.enums[name] && !schema.enums[name].includes(cell)) fail(400);
      if (name === "parameters_hash" && cell && !/^[0-9a-f]{64}$/.test(cell)) fail(400);
      if (name === "oura_sleep_algorithm_versions" && cell && !/^[a-zA-Z0-9._;-]{1,64}$/.test(cell)) fail(400);
    }
  }
}

export function createHandler({ env, fetch: fetcher, now = () => Date.now(), operations, log = () => {}, waitUntil }) {
  const enabled = feature => env[feature + "_ENABLED"] === "true";
  const callback = () => env.SERVICE_URL.replace(/\/$/, "") + "/oauth/callback";
  async function hmac() {
    if (!env.OAUTH_STATE_SECRET || env.OAUTH_STATE_SECRET.length < 43) fail(503);
    return crypto.subtle.importKey("raw", encoder.encode(env.OAUTH_STATE_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
  }
  async function sign(flow) {
    const value = B64(encoder.encode(JSON.stringify(flow)));
    return value + "." + B64(await crypto.subtle.sign("HMAC", await hmac(), encoder.encode(value)));
  }
  async function verify(value) {
    if (typeof value !== "string" || value.length > 4096) fail(400);
    const [payload, signature, extra] = value.split(".");
    try {
      if (extra || !await crypto.subtle.verify("HMAC", await hmac(), decode(signature), encoder.encode(payload))) fail(400);
      const flow = JSON.parse(new TextDecoder().decode(decode(payload)));
      if (flow.expires < now() || flow.expires > now() + 610000) fail(400);
      loopback(flow.return_uri); return flow;
    } catch (error) { if (error instanceof Failure) throw error; fail(400); }
  }
  async function upstream(url, options) {
    let response;
    try { response = await fetcher(url, { ...options, redirect: "error", signal: AbortSignal.timeout(25000) }); }
    catch { fail(502); }
    if (!response.ok) {
      // Retry-After is handled on direct Oura data requests by the desktop.
      const retry = Number(response.headers.get("retry-after"));
      fail([400, 401].includes(response.status) ? 401 : response.status === 429 ? 429 : 502,
        response.status === 429 ? (Number.isInteger(retry) && retry > 0 && retry <= 86400 ? retry : 60) : undefined);
    }
    return response;
  }
  async function token(fields) {
    if (!env.OURA_CLIENT_ID || !env.OURA_CLIENT_SECRET) fail(503);
    const response = await upstream("https://api.ouraring.com/oauth/token", {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ ...fields, client_id: env.OURA_CLIENT_ID, client_secret: env.OURA_CLIENT_SECRET })
    });
    const value = await response.json();
    if (typeof value.access_token !== "string" || typeof value.refresh_token !== "string" ||
        !Number.isFinite(value.expires_in) || value.expires_in <= 0) fail(502);
    return json({ access_token: value.access_token, refresh_token: value.refresh_token, expires_in: value.expires_in });
  }
  async function rpc(name, params) {
    let serverKey = env.SUPABASE_SERVICE_ROLE_KEY;
    if (env.SUPABASE_SECRET_KEYS) {
      try { serverKey = JSON.parse(env.SUPABASE_SECRET_KEYS).default || serverKey; }
      catch { fail(503); }
    }
    if (!env.SUPABASE_URL || !serverKey) fail(503);
    let response;
    try {
      response = await fetcher(env.SUPABASE_URL + "/rest/v1/rpc/" + name, {
        method: "POST", redirect: "error", signal: AbortSignal.timeout(25000),
        headers: { "Content-Type": "application/json", apikey: serverKey,
          ...(serverKey.split(".").length === 3 ? { Authorization: "Bearer " + serverKey } : {}) }, body: JSON.stringify(params)
      });
    } catch { fail(502); }
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      if (error.code === "P0001" && error.message === "quota_exceeded") fail(429, 86400);
      fail(error.code === "23505" ? 409 : error.code === "28000" ? 401 : 502);
    }
    return response.json();
  }
  const ops = operations ?? createOperations({ rpc, secret: env.OAUTH_STATE_SECRET, now, fail });
  const admitted = new WeakSet();
  const admit = async (request, family, subject) => {
    await ops.admit(family, subject); admitted.add(request);
  };
  const oauthSubject = data => data?.refresh_token ?? data?.access_token ?? data?.token ?? data?.flow ?? data?.state ?? "invalid";
  const google = googleOAuth({ env, now, fail, json, body, exact, loopback, sign, verify, upstream, B64, SECRET,
    admit: (request, subject) => admit(request, "oauth", subject), oauthSubject });
  const handle = async request => {
    try {
      const url = new URL(request.url);
      const route = url.pathname.replace(/^.*\/qs-service/, "");
      if (request.method === "GET" && route === "/status") return json({
        oura_enabled: enabled("OURA"), google_health_enabled: enabled("GOOGLE_HEALTH"), research_enabled: enabled("RESEARCH"),
        operations_version: 1,
        operator: env.RESEARCH_OPERATOR || "", privacy_url: env.PRIVACY_URL || "",
        notice_version: env.RESEARCH_NOTICE_VERSION || "", notice_hash: env.RESEARCH_NOTICE_HASH || ""
      });
      if (route.startsWith("/google/oauth/")) return await google(request, url, route);
      if (route.startsWith("/oauth/")) {
        if (!enabled("OURA")) fail(503);
        if (request.method === "GET" && route === "/oauth/callback") {
          await admit(request, "oauth", (url.searchParams.get("state") || "invalid").slice(0,4096));
          const flow = await verify(url.searchParams.get("state"));
          if (flow.provider !== "oura") fail(400);
          const target = loopback(flow.return_uri);
          target.searchParams.set("state", flow.state);
          const code = url.searchParams.get("code");
          if (!url.searchParams.has("error") && code && code.length <= 4096) target.searchParams.set("code", code);
          else target.searchParams.set("error", "access_denied");
          return new Response(null, { status: 303, headers: { Location: target.toString(), "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" } });
        }
        if (request.method !== "POST") fail(405);
        const data = await body(request);
        await admit(request, "oauth", oauthSubject(data));
        if (route === "/oauth/start") {
          exact(data, ["return_uri", "state", "code_challenge"]);
          loopback(data.return_uri);
          if (!SECRET.test(data.state) || !/^[A-Za-z0-9_-]{43}$/.test(data.code_challenge)) fail(400);
          const flow = await sign({ ...data, provider: "oura", expires: now() + 600000 });
          const authorize = new URL("https://cloud.ouraring.com/oauth/authorize");
          for (const [key, value] of Object.entries({ client_id: env.OURA_CLIENT_ID, response_type: "code", redirect_uri: callback(),
            scope: "daily", state: flow, code_challenge: data.code_challenge, code_challenge_method: "S256" })) authorize.searchParams.set(key, value);
          return json({ authorize_url: authorize.toString(), flow });
        }
        if (route === "/oauth/exchange") {
          exact(data, ["code", "code_verifier", "flow"]);
          const flow = await verify(data.flow);
          if (flow.provider !== "oura") fail(400);
          if (!SECRET.test(data.code_verifier) || typeof data.code !== "string" || data.code.length > 4096) fail(400);
          const challenge = B64(await crypto.subtle.digest("SHA-256", encoder.encode(data.code_verifier)));
          if (challenge !== flow.code_challenge) fail(400);
          return await token({ grant_type: "authorization_code", code: data.code, code_verifier: data.code_verifier, redirect_uri: callback() });
        }
        if (route === "/oauth/refresh") {
          exact(data, ["refresh_token"]);
          if (typeof data.refresh_token !== "string" || !data.refresh_token || data.refresh_token.length > 8192) fail(400);
          return await token({ grant_type: "refresh_token", refresh_token: data.refresh_token });
        }
        if (route === "/oauth/revoke") {
          exact(data, ["access_token"]);
          if (typeof data.access_token !== "string" || !data.access_token || data.access_token.length > 8192) fail(400);
          await upstream("https://api.ouraring.com/oauth/revoke?access_token=" + encodeURIComponent(data.access_token), { method: "GET" });
          return json({ revoked: true });
        }
        fail(404);
      }
      if (route.startsWith("/research/")) {
        const { id, secret } = credentials(request); const hash = await sha256(secret);
        if (route === "/research/participant" && request.method === "DELETE") {
          // Keep deletion available even when new research uploads are disabled.
          await admit(request, "delete", id + ":" + hash);
          if (!await rpc("qs_delete", { p_id: id, p_hash: hash })) fail(401);
          return json({ deleted: true });
        }
        if (!enabled("RESEARCH") || !env.PRIVACY_URL || !env.RESEARCH_OPERATOR) fail(503);
        if (route === "/research/participant" && request.method === "POST") {
          await admit(request, "register", id + ":" + hash);
          if (!await rpc("qs_register", { p_id: id, p_hash: hash })) fail(410);
          return json({ registered: true });
        }
        if (route === "/research/submissions" && request.method === "POST") {
          await admit(request, "submit", id + ":" + hash);
          const data = await body(request, 8 * 1024 * 1024); validatePackage(data, env);
          return json(await rpc("qs_submit", { p_id: id, p_hash: hash, p_submission: data.submission_id,
            p_payload: data, p_payload_hash: await sha256(JSON.stringify(data)) }));
        }
      }
      fail(404);
    } catch (error) {
      const response = json({ error: "Request could not be completed." }, error instanceof Failure ? error.status : 500);
      if (error instanceof Failure && error.retryAfter) response.headers.set("Retry-After", String(error.retryAfter));
      return response;
    }
  };
  return async request => {
    const start = now(), request_id = crypto.randomUUID(), route = routeLabel(request);
    const response = await handle(request);
    const duration_ms = Math.max(0, Math.min(300000, Math.round(now()-start)));
    // Explicit allowlist; never log URLs, query strings, headers, bodies, identities or errors.
    try { log({ event: "qs_request", version: 1, request_id, route, status: response.status, duration_ms }); } catch { /* Logging must not fail a request. */ }
    if (admitted.has(request) && route !== "unknown") {
      const task = ops.record(route, response.status, duration_ms).catch(() => {
        try { log({ event: "qs_monitoring_failure", version: 1, request_id, route }); } catch { /* No sensitive errors. */ }
      });
      if (waitUntil) waitUntil(task); else await task;
    }
    return response;
  };
}
