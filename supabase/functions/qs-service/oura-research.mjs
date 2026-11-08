// Access-token-only research authorization monitoring. Never fetch/store measurements.
const enc = new TextEncoder();
const b64 = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes)));
const un64 = text => Uint8Array.from(atob(text), c => c.charCodeAt(0));
const hex = bytes => Array.from(new Uint8Array(bytes), x => x.toString(16).padStart(2, "0")).join("");
export function ouraResearch({ env, rpc, fetch: fetcher, now, fail }) {
  const hash = async text => hex(await crypto.subtle.digest("SHA-256", enc.encode(text)));
  async function proofKey() {
    if (!env.OAUTH_STATE_SECRET || env.OAUTH_STATE_SECRET.length < 43) fail(503);
    return crypto.subtle.importKey("raw", enc.encode("oura-research-access-v1/" + env.OAUTH_STATE_SECRET),
      { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
  }
  async function receipt(value) {
    const data = b64(enc.encode(JSON.stringify({ token: await hash(value.access_token),
      expires: now() + value.expires_in * 1000 })));
    return data + "." + b64(await crypto.subtle.sign("HMAC", await proofKey(), enc.encode(data)));
  }
  async function expiry(access, proof) {
    try {
      if (typeof proof !== "string" || proof.length > 1024) fail(400);
      const [data, signature, extra] = proof.split(".");
      if (extra || !await crypto.subtle.verify("HMAC", await proofKey(), un64(signature), enc.encode(data))) fail(400);
      const parsed = JSON.parse(new TextDecoder().decode(un64(data)));
      if (parsed.token !== await hash(access) || !Number.isFinite(parsed.expires) ||
          parsed.expires <= now() || parsed.expires > now() + 366 * 86400000) fail(400);
      return new Date(parsed.expires).toISOString();
    } catch { fail(400); }
  }
  async function key() {
    try {
      const bytes = un64(env.OURA_RESEARCH_TOKEN_KEY || "");
      if (bytes.length !== 32) fail(503);
      return await crypto.subtle.importKey("raw", bytes, "AES-GCM", false, ["encrypt", "decrypt"]);
    } catch { fail(503); }
  }
  const aad = (id, generation) => enc.encode("oura-research-token-v1/" + id + "/" + generation);
  async function seal(token, id, generation) {
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    return { nonce: b64(nonce), body: b64(await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: nonce, additionalData: aad(id, generation) }, await key(), enc.encode(token))) };
  }
  async function open(record) {
    return new TextDecoder().decode(await crypto.subtle.decrypt({ name: "AES-GCM",
      iv: un64(record.token.nonce), additionalData: aad(record.id, record.generation) },
    await key(), un64(record.token.body)));
  }
  async function unauthorized(response) {
    // Oura also uses 401 for a denied scope. Only an explicit invalid-token
    // problem confirms revocation; ambiguous errors pause rather than delete.
    const reader = response.body?.getReader();
    if (!reader) return "unavailable";
    try {
      const chunks = []; let size = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 4096) return "unavailable";
        chunks.push(value);
      }
      const bytes = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      const problem = JSON.parse(new TextDecoder().decode(bytes));
      if (problem?.title === "Missing Scopes" || problem?.error === "insufficient_scope")
        return "scope_unavailable";
      return problem?.title === "Invalid Access Token" || problem?.error === "invalid_token"
        ? "revoked" : "unavailable";
    } catch { return "unavailable"; }
    finally { await reader.cancel().catch(() => {}); }
  }
  async function probe(access, expires) {
    if (env.OURA_ENABLED !== "true") return "unavailable";
    if (Date.parse(expires) <= now() + 120000) return "expired";
    for (const type of ["sleep", "workout"]) {
      let response;
      try {
        // A pre-Oura date window cannot contain contemporary health records.
        // Discard the body even if a provider returns unexpected data.
        response = await fetcher("https://api.ouraring.com/v2/usercollection/" + type +
          "?start_date=1900-01-01&end_date=1900-01-02", {
          headers: { Authorization: "Bearer " + access,
            "User-Agent": "QuantifiedCognition/0.23.0 (research-authorization)" }, redirect: "error",
          signal: AbortSignal.timeout(5000)
        });
        if (response.status === 401) {
          const problem = await unauthorized(response);
          if (problem === "scope_unavailable") continue;
          return problem;
        }
        await response.body?.cancel();
      } catch { return "unavailable"; }
      if (response.status === 200) return "verified";
      if (response.status !== 403) return "unavailable";
    }
    // Missing scopes/membership is ambiguous: stop use without inventing withdrawal.
    return "scope_unavailable";
  }
  async function bind(id, secret, data) {
    id = id.toLowerCase();
    if (typeof data.access_token !== "string" || !data.access_token || data.access_token.length > 8192 ||
        typeof data.receipt_secret !== "string" || !/^[A-Za-z0-9_-]{43,128}$/.test(data.receipt_secret)) fail(400);
    const expires = await expiry(data.access_token, data.authorization_proof);
    const generation = crypto.randomUUID();
    await rpc("qs_oura_authorization_bind", { p_id: id, p_hash: await hash(secret), p_generation: generation,
      p_token: await seal(data.access_token, id, generation), p_expires: expires,
      p_receipt_hash: await hash(data.receipt_secret), p_allow_create: env.RESEARCH_ENABLED === "true" });
    return rpc("qs_oura_authorization_result", { p_id: id, p_generation: generation,
      p_result: await probe(data.access_token, expires) });
  }
  async function job(authorization) {
    // Constant-length digest comparison avoids comparing the secret with early exits.
    if (!env.OURA_RESEARCH_JOB_SECRET || env.OURA_RESEARCH_JOB_SECRET.length < 43 ||
        await hash(authorization || "") !== await hash("Bearer " + env.OURA_RESEARCH_JOB_SECRET)) fail(401);
    const records = await rpc("qs_oura_authorization_claim", {});
    if (!Array.isArray(records) || records.length > 100) fail(503);
    let offset = 0, checked = 0;
    await Promise.all(Array.from({ length: Math.min(records.length, 8) }, async () => {
      while (offset < records.length) {
        const record = records[offset++];
        let result;
        try { result = await probe(await open(record), record.expires_at); }
        catch { result = "unavailable"; }
        await rpc("qs_oura_authorization_result", { p_id: record.id,
          p_generation: record.generation, p_result: result });
        checked++;
      }
    }));
    return { checked };
  }
  return { receipt, bind, job };
}
