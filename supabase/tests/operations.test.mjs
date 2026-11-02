import test from "node:test";
import assert from "node:assert/strict";
import { createHandler } from "../functions/qs-service/service.mjs";

const env = { OURA_ENABLED: "true", GOOGLE_HEALTH_ENABLED: "true", RESEARCH_ENABLED: "true",
  OURA_CLIENT_ID: "synthetic", OURA_CLIENT_SECRET: "provider-secret",
  GOOGLE_CLIENT_ID: "synthetic", GOOGLE_CLIENT_SECRET: "provider-secret",
  OAUTH_STATE_SECRET: "s".repeat(48), SUPABASE_SERVICE_ROLE_KEY: "server-only",
  SUPABASE_URL: "https://example.supabase.co", SERVICE_URL: "https://example.supabase.co/functions/v1/qs-service",
  PRIVACY_URL: "https://example.org/privacy", RESEARCH_OPERATOR: "Synthetic" };
const id = "00000000-0000-4000-8000-000000000001", secret = "c".repeat(43);
const request = (route, data, method="POST", extra={}) => new Request(env.SERVICE_URL+route, {
  method, headers: { "Content-Type": "application/json", authorization: `Bearer ${id}:${secret}`, ...extra },
  ...(data === undefined && method !== "DELETE" ? {} : {
    body: JSON.stringify(data ?? {receipt_secret:secret}) }) });
function harness(options={}) {
  const calls=[], logs=[];
  const handler=createHandler({ env: { ...env,...options.env }, now: options.now ?? (()=>1793059200000),
    log: event=>logs.push(event), waitUntil: options.waitUntil,
    fetch: async (url,init)=> {
      const data = typeof init.body === "string" ? JSON.parse(init.body) : null;
      calls.push({ url,data,init });
      if (url.endsWith("/qs_admit")) return options.admit?.(data) ?? Response.json({ allowed:true,retry_after:0 });
      if (url.endsWith("/qs_ops_record")) return options.record?.() ?? Response.json(null);
      if (url.endsWith("/qs_delete_request")) return Response.json({active_storage_deleted:true,complete:true});
      if (url.endsWith("/qs_register")) return options.register?.() ?? Response.json(true);
      return Response.json({ access_token:"access",refresh_token:"refresh",expires_in:3600 });
    } });
  return { handler,calls,logs };
}
test("production admission denies provider calls with Retry-After and sanitized logs",async()=> {
  for (const route of ["/oauth/refresh","/google/oauth/refresh"]) {
    const h=harness({ admit:()=>Response.json({ allowed:false,retry_after:42 }) });
    const result=await h.handler(request(route,{ refresh_token:"private-token" }));
    assert.equal(result.status,429); assert.equal(result.headers.get("retry-after"),"42");
    assert.equal(h.calls.length,1);
    assert.equal(h.calls[0].data.p_family,"oauth");
    assert.match(h.calls[0].data.p_subject,/^[a-f0-9]{64}$/);
    const logs=JSON.stringify(h.logs);
    for (const value of ["private-token",secret,id,"authorization","example.supabase.co"]) assert.ok(!logs.includes(value));
  }
});
test("unavailable or malformed quota store fails closed before provider/storage work",async()=> {
  for (const admit of [()=>{throw Error("secret infrastructure details");},
    ()=>Response.json({code:"XX000"},{status:500}),()=>Response.json({allowed:"true"})]) {
    const h=harness({admit});
    const result=await h.handler(request("/research/participant",undefined,"DELETE"));
    assert.equal(result.status,503); assert.equal(h.calls.length,1);
    assert.ok(!JSON.stringify(h.logs).includes("secret infrastructure"));
  }
});
test("closed features do no quota, provider or storage work; deletion remains independently budgeted",async()=> {
  const h=harness({env:{OURA_ENABLED:"false",GOOGLE_HEALTH_ENABLED:"false",RESEARCH_ENABLED:"false"}});
  for (const route of ["/oauth/refresh","/google/oauth/refresh","/research/participant","/research/submissions"]) {
    assert.equal((await h.handler(request(route,{}))).status,503);
  }
  assert.equal(h.calls.length,0);
  const status=await h.handler(request("/status",undefined,"GET"));
  assert.equal((await status.json()).operations_version,1);
  assert.equal(h.calls.length,0);
  assert.equal((await h.handler(request("/research/participant",undefined,"DELETE"))).status,200);
  assert.deepEqual(h.calls.map(c=>c.url.split("/").at(-1)),["qs_admit","qs_delete_request","qs_ops_record"]);
  assert.equal(h.calls[0].data.p_family,"delete");
});
test("actor hashes ignore spoofed IP headers and rotate by UTC day",async()=> {
  const a=harness(), b=harness({now:()=>1793145600000});
  await a.handler(request("/oauth/refresh",{refresh_token:"private-token"},"POST",{"x-forwarded-for":"1.2.3.4"}));
  await a.handler(request("/google/oauth/refresh",{refresh_token:"private-token"},"POST",{"x-forwarded-for":"9.8.7.6"}));
  await b.handler(request("/oauth/refresh",{refresh_token:"private-token"}));
  const hashes=a.calls.filter(c=>c.url.endsWith("/qs_admit")).map(c=>c.data.p_subject);
  assert.equal(hashes[0],hashes[1]);
  assert.notEqual(hashes[0],b.calls[0].data.p_subject);
  assert.ok(!JSON.stringify(a.calls.filter(c=>c.url.endsWith("/qs_admit"))).includes("private-token"));
});
test("OAuth streamed byte limit is 16 KiB; uploads are admitted before reading their body",async()=> {
  const h=harness();
  assert.equal((await h.handler(request("/oauth/refresh",{refresh_token:"x".repeat(17000)}))).status,413);
  assert.equal(h.calls.length,0);
  const denied=harness({admit:()=>Response.json({allowed:false,retry_after:60})});
  assert.equal((await denied.handler(request("/research/submissions",{secret:"x".repeat(8*1024*1024)}))).status,429);
  assert.equal(denied.calls.length,1);
});
test("storage quota maps to 429; metric failure does not change successful response",async()=> {
  const full=harness({register:()=>Response.json({code:"P0001",message:"quota_exceeded"},{status:400})});
  const result=await full.handler(request("/research/participant",{}));
  assert.equal(result.status,429); assert.equal(result.headers.get("retry-after"),"86400");
  const tasks=[];
  const h=harness({record:()=>{throw Error("private database failure");},waitUntil:task=>tasks.push(task)});
  assert.equal((await h.handler(request("/research/participant",undefined,"DELETE"))).status,200);
  await Promise.all(tasks);
  assert.equal(h.logs.at(-1).event,"qs_monitoring_failure");
  assert.ok(!JSON.stringify(h.logs).includes("private database failure"));
});
