// End-to-end synthetic HTTP -> production admission -> real PostgreSQL RPCs.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile,readdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { createHandler } from "../functions/qs-service/service.mjs";
import schema from "../functions/qs-service/schema.json" with {type:"json"};
import { protocol,evidence,notice } from "./research-fixtures.mjs";
import { envelope } from "./encrypted-fixtures.mjs";

test("real HTTP enrollment gate, explicit upload, and deletion status with all feature flags closed",async()=>{
  const {PGlite}=await import(pathToFileURL(process.env.PGLITE_TEST_MODULE).href);
  const db=new PGlite(), logs=[];
  const id=crypto.randomUUID(), publicId=crypto.randomUUID(), secret="s".repeat(43), receiptSecret="r".repeat(43);
  const env={SUPABASE_URL:"https://database.invalid",SUPABASE_SERVICE_ROLE_KEY:"synthetic",
    SERVICE_URL:"https://example.org/qs-service",RESEARCH_OPERATOR:"Synthetic operator",
    PRIVACY_URL:"https://example.org/privacy",RESEARCH_NOTICE_VERSION:notice.version,RESEARCH_NOTICE_HASH:notice.hash,
    OAUTH_STATE_SECRET:"o".repeat(48),RESEARCH_ENABLED:"true",OURA_ENABLED:"false",GOOGLE_HEALTH_ENABLED:"false"};
  const request=(route,data,method="POST",key=secret)=>new Request(env.SERVICE_URL+route,{
    method,headers:{"content-type":"application/json",authorization:`Bearer ${id}:${key}`},
    ...(data===undefined?{}:{body:JSON.stringify(data)})});
  const rpcNames={qs_admit:2,qs_ops_record:3,qs_register:2,qs_enrollment_information:0,qs_enroll:3,
    qs_submit:5,qs_delete_request:3,qs_deletion_status:2};
  async function fetch(url,options) {
    const name=url.split("/").at(-1), args=JSON.parse(options.body);
    assert.ok(name in rpcNames);assert.equal(Object.keys(args).length,rpcNames[name]);
    const keys=Object.keys(args);
    await db.exec("set role service_role");
    try {
      const query=`select public.${name}(${keys.map((key,i)=>`${key}=>$${i+1}`).join(",")}) result`;
      return Response.json((await db.query(query,Object.values(args))).rows[0].result);
    } catch(error) { return Response.json({code:error.code,message:error.message},{status:400}); }
    finally {await db.exec("reset role");}
  }
  try {
    await db.exec("create role anon;create role authenticated;create role service_role bypassrls;");
    const directory=new URL("../migrations/",import.meta.url);
    for(const name of (await readdir(directory)).filter(n=>n.endsWith(".sql")).sort())
      await db.exec(await readFile(new URL(name,directory),"utf8"));
    // Independent operational tests cover caps; deliberately invalid requests
    // should not require advancing the clock during this end-to-end walkthrough.
    await db.exec("update qs_private.quota_policy set actor_minute=100,actor_day=100,global_minute=100,global_day=1000");
    const handler=createHandler({env,fetch,log:value=>logs.push(value)});
    // Flag true alone cannot approve enrollment; even direct calls fail closed.
    assert.equal((await handler(request("/research/enrollment-information",undefined,"GET"))).status,503);
    await protocol(db);
    assert.equal((await handler(request("/research/enrollment-information",undefined,"GET"))).status,200);
    assert.equal((await handler(request("/research/participant",{}))).status,200);
    const metadata=Object.fromEntries(schema.metadata.map(k=>[k,""]));
    Object.assign(metadata,{research_participant_id:publicId,research_consent_active:true,
      sharing_current_choice:"opt_in",sharing_notice_version:notice.version,sharing_notice_hash:notice.hash});
    const packet={schema_version:5,enrollment_id:id,submission_id:crypto.randomUUID(),created_at:new Date().toISOString(),
      metadata,columns:schema.columns,rows:[schema.columns.map(k=>k==="date"?"2026-10-01":"")]};
    assert.equal((await handler(request("/research/submissions",envelope(packet)))).status,403);
    const signed=evidence(id,{participant_id:publicId});
    assert.equal((await handler(request("/research/enrollment",{...signed,explicit_consent:false}))).status,400);
    const missing={...signed};delete missing.explicit_consent;
    assert.equal((await handler(request("/research/enrollment",missing))).status,400);
    assert.equal((await handler(request("/research/enrollment",{...signed,signature:"extra pseudonym"}))).status,400);
    assert.equal((await handler(request("/research/enrollment",{...signed,signature_method:"typed_pseudonym"}))).status,400);
    const joined=await handler(request("/research/enrollment",signed));assert.equal(joined.status,200);
    const first=await joined.json();assert.equal(first.enrollment_id,id);
    assert.deepEqual(await (await handler(request("/research/enrollment",signed))).json(),first);
    assert.equal((await handler(request("/research/enrollment",{...signed,participant_id:crypto.randomUUID()}))).status,409);
    const encrypted=envelope(packet);
    assert.equal((await handler(request("/research/submissions",packet))).status,400);
    const sent=await handler(request("/research/submissions",encrypted));assert.equal(sent.status,200);
    assert.deepEqual(await (await handler(request("/research/submissions",encrypted))).json(),await sent.json());
    const closed=createHandler({env:{...env,RESEARCH_ENABLED:"false"},fetch,log:value=>logs.push(value)});
    assert.equal((await closed(request("/research/enrollment",signed))).status,503);
    const deleted=await closed(request("/research/participant",{receipt_secret:receiptSecret},"DELETE"));
    assert.equal(deleted.status,200);assert.equal((await deleted.json()).complete,true);
    const status=await closed(request("/research/deletion-status",{},"POST",receiptSecret));
    assert.equal(status.status,200);assert.equal((await status.json()).active_storage_deleted,true);
    assert.equal((await closed(request("/research/deletion-status",{},"POST","x".repeat(43)))).status,401);
    assert.equal((await db.query("select capability_hash from qs_private.participants where id=$1",[id])).rows[0].capability_hash,null);
    assert.equal((await handler(request("/research/submissions",encrypted))).status,401);
    for(const value of ["extra pseudonym",publicId,id,secret,receiptSecret])assert.ok(!JSON.stringify(logs).includes(value));
  } finally {await db.close();}
});
