// OAuth broker only. Health measurements go directly from Google to the desktop.
export function googleOAuth({ env, now, fail, json, body, exact, loopback, sign, verify, upstream, B64, SECRET, admit, oauthSubject }) {
  const scopes = ["https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"];
  const callback = () => env.SERVICE_URL.replace(/\/$/, "") + "/google/oauth/callback";
  const validToken = value => typeof value === "string" && value.length > 0 && value.length <= 8192;
  async function tokens(fields) {
    const response = await upstream("https://oauth2.googleapis.com/token", {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ ...fields, client_id: env.GOOGLE_CLIENT_ID, client_secret: env.GOOGLE_CLIENT_SECRET })
    });
    const value = await response.json();
    if (!validToken(value.access_token) || !Number.isFinite(value.expires_in) || value.expires_in <= 0 ||
        (value.refresh_token !== undefined && !validToken(value.refresh_token))) fail(502);
    if (fields.grant_type === "authorization_code" && (!validToken(value.refresh_token) || !value.scope)) fail(502);
    if (value.scope !== undefined && (typeof value.scope !== "string" || !value.scope.split(" ").length ||
        value.scope.split(" ").some(s => !scopes.includes(s)))) fail(502);
    return json({ access_token: value.access_token, expires_in: value.expires_in,
      ...(value.refresh_token ? { refresh_token: value.refresh_token } : {}),
      ...(value.scope ? { scope: value.scope } : {}) });
  }
  return async (request, url, route) => {
    if (env.GOOGLE_HEALTH_ENABLED !== "true" || !env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET) fail(503);
    if (request.method === "GET" && route === "/google/oauth/callback") {
      await admit(request, (url.searchParams.get("state") || "invalid").slice(0,4096));
      const flow = await verify(url.searchParams.get("state"));
      if (flow.provider !== "google_health") fail(400);
      const target = loopback(flow.return_uri);
      target.searchParams.set("state", flow.state);
      const code = url.searchParams.get("code");
      if (!url.searchParams.has("error") && code && code.length <= 4096) target.searchParams.set("code", code);
      else target.searchParams.set("error", "access_denied");
      return new Response(null, { status: 303, headers: { Location: target.toString(),
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" } });
    }
    if (request.method !== "POST") fail(405);
    const data = await body(request);
    await admit(request, oauthSubject(data));
    if (route === "/google/oauth/start") {
      exact(data, ["return_uri", "state", "code_challenge"]);
      loopback(data.return_uri);
      if (!SECRET.test(data.state) || !/^[A-Za-z0-9_-]{43}$/.test(data.code_challenge)) fail(400);
      const flow = await sign({ ...data, provider: "google_health", expires: now() + 600000 });
      const authorize = new URL("https://accounts.google.com/o/oauth2/v2/auth");
      for (const [key, value] of Object.entries({ client_id: env.GOOGLE_CLIENT_ID,
        response_type: "code", redirect_uri: callback(), scope: scopes.join(" "), state: flow,
        code_challenge: data.code_challenge, code_challenge_method: "S256",
        access_type: "offline", prompt: "consent" })) authorize.searchParams.set(key, value);
      return json({ authorize_url: authorize.toString(), flow });
    }
    if (route === "/google/oauth/exchange") {
      exact(data, ["code", "code_verifier", "flow"]);
      const flow = await verify(data.flow);
      if (flow.provider !== "google_health" || !SECRET.test(data.code_verifier) ||
          typeof data.code !== "string" || !data.code || data.code.length > 4096) fail(400);
      const challenge = B64(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(data.code_verifier)));
      if (challenge !== flow.code_challenge) fail(400);
      return tokens({ grant_type: "authorization_code", code: data.code,
        code_verifier: data.code_verifier, redirect_uri: callback() });
    }
    if (route === "/google/oauth/refresh") {
      exact(data, ["refresh_token"]);
      if (!validToken(data.refresh_token)) fail(400);
      return tokens({ grant_type: "refresh_token", refresh_token: data.refresh_token });
    }
    if (route === "/google/oauth/revoke") {
      exact(data, ["token"]);
      if (!validToken(data.token)) fail(400);
      await upstream("https://oauth2.googleapis.com/revoke", {
        method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ token: data.token })
      });
      return json({ revoked: true });
    }
    fail(404);
  };
}
