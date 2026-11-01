// Shared database admission, independent of edge-instance lifetime or client IP headers.
const encoder = new TextEncoder();
export function createOperations({ rpc, secret, now, fail }) {
  let key;
  return {
    async admit(family, subject) {
      if (!secret || secret.length < 43) fail(503);
      key ??= crypto.subtle.importKey("raw", encoder.encode(secret),
        { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
      const day = new Date(now()).toISOString().slice(0, 10);
      const digest = await crypto.subtle.sign("HMAC", await key,
        encoder.encode(JSON.stringify(["qs-quota-v1", day, family, subject])));
      const hash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
      let result;
      try { result = await rpc("qs_admit", { p_family: family, p_subject: hash }); }
      catch { fail(503); } // Provider/storage work must fail closed when admission is unavailable.
      if (result?.allowed === true) return;
      if (result?.allowed === false && Number.isInteger(result.retry_after) &&
          result.retry_after > 0 && result.retry_after <= 86400) fail(429, result.retry_after);
      fail(503);
    },
    async record(route, status, duration) {
      await rpc("qs_ops_record", { p_route: route, p_status: status, p_duration_ms: duration });
    }
  };
}

export function routeLabel(request) {
  const route = new URL(request.url).pathname.replace(/^.*\/qs-service/, "");
  if (route === "/status") return "status";
  if (/^\/oauth\/(start|callback|exchange|refresh|revoke)$/.test(route)) return "oura_oauth";
  if (/^\/google\/oauth\/(start|callback|exchange|refresh|revoke)$/.test(route)) return "google_oauth";
  if (route === "/research/participant") return request.method === "DELETE" ? "delete" : "register";
  if (route === "/research/submissions") return "submit";
  return "unknown";
}
