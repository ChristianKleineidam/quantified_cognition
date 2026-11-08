import test from "node:test";
import assert from "node:assert/strict";
import { ouraResearch } from "../functions/qs-service/oura-research.mjs";

const id="00000000-0000-4000-8000-000000000001";
const env={OURA_ENABLED:"true",RESEARCH_ENABLED:"true",OAUTH_STATE_SECRET:"s".repeat(48),
  OURA_RESEARCH_JOB_SECRET:"j".repeat(48),OURA_RESEARCH_TOKEN_KEY:Buffer.alloc(32,7).toString("base64")};
const fail=code=>{throw new Error(String(code));};
function setup(fetcher=async()=>new Response("not inspected",{status:200})) {
  const calls=[];
  const rpc=async(name,params)=>{calls.push([name,params]);
    if(name.endsWith("claim")) return calls.filter(c=>c[0].endsWith("bind")).slice(-1).map(([,p])=>({
      id:p.p_id,generation:p.p_generation,token:p.p_token,expires_at:p.p_expires}));
    return {state:"active"};};
  return {calls,monitor:ouraResearch({env,rpc,fetch:fetcher,now:()=>1800000000000,fail})};
}
async function bind(monitor) {
  const access_token="synthetic-access-only";
  return monitor.bind(id,"u".repeat(43),{access_token,
    authorization_proof:await monitor.receipt({access_token,expires_in:86400}),receipt_secret:"r".repeat(43)});
}
test("encrypted monitor credentials exclude refresh tokens and bind ciphertext to participant/generation",async()=>{
  const seen=[];const {monitor,calls}=setup(async(url,options)=>{
    assert.match(options.headers["User-Agent"],/^QuantifiedCognition\//);
    seen.push({url,token:options.headers.Authorization});
    return new Response("ignored measurements",{status:200});
  });
  await bind(monitor);
  const parameters=calls[0][1];
  assert.ok(!JSON.stringify(parameters).includes("synthetic-access-only"));
  assert.ok(!JSON.stringify(parameters).includes("refresh"));
  assert.deepEqual(Object.keys(parameters.p_token).sort(),["body","nonce"]);
  await monitor.job("Bearer "+env.OURA_RESEARCH_JOB_SECRET);
  assert.equal(seen.length,2);assert.equal(seen[1].token,"Bearer synthetic-access-only");
  assert.ok(seen.every(s=>s.url.includes("1900-01-01")));
  parameters.p_generation=crypto.randomUUID();
  await monitor.job("Bearer "+env.OURA_RESEARCH_JOB_SECRET);
  assert.equal(calls.at(-1)[1].p_result,"unavailable");
});
test("forged/expired authorization proofs and unauthenticated jobs cannot bind or probe",async()=>{
  const {monitor,calls}=setup();
  await assert.rejects(monitor.job("Bearer wrong"),/401/);
  await assert.rejects(monitor.bind(id,"x",{access_token:"wrong",authorization_proof:"fake",receipt_secret:"r".repeat(43)}),/400/);
  assert.equal(calls.length,0);
});
for(const [statuses,expected] of [[[401],"unavailable"],[[403,200],"verified"],
  [[403,403],"scope_unavailable"],[[429],"unavailable"],[[503],"unavailable"]]) {
  test(`provider status ${statuses} yields ${expected} without reading health bodies`,async()=>{
    let i=0;const {monitor,calls}=setup(async()=>new Response("must not read",{status:statuses[i++]}));
    await bind(monitor);assert.equal(calls.at(-1)[1].p_result,expected);
  });
}
test("Oura 401 scope denials preserve Workout-only grants and never invent revocation",async()=>{
  for(const [problems,expected] of [
    [[{title:"Missing Scopes"},null],"verified"],
    [[{error:"insufficient_scope"},{title:"Missing Scopes"}],"scope_unavailable"],
    [[{title:"Invalid Access Token"}],"revoked"],
    [[{error:"invalid_token"}],"revoked"],
    [[{title:"Unexpected provider problem"}],"unavailable"],
    [["x".repeat(4097)],"unavailable"]
  ]) {
    let index=0;const {monitor,calls}=setup(async()=>{
      const problem=problems[index++];
      return new Response(problem===null?"discarded health body":JSON.stringify(problem),
        {status:problem===null?200:401});
    });
    await bind(monitor);assert.equal(calls.at(-1)[1].p_result,expected);
  }
});
test("expired access and disabled Oura pause without provider requests or inferred withdrawal",async()=>{
  const calls=[];let fetches=0;
  const monitor=ouraResearch({env:{...env,OURA_ENABLED:"false"},now:()=>1800000000000,fail,
    fetch:async()=>{fetches++;throw new Error("must not probe");},
    rpc:async(n,p)=>{calls.push([n,p]);return {state:"paused"};}});
  await bind(monitor);assert.equal(calls.at(-1)[1].p_result,"unavailable");assert.equal(fetches,0);
});
