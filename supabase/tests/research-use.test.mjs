import test from "node:test";
import assert from "node:assert/strict";
import {createHandler} from "../functions/qs-service/service.mjs";

test("workspace token permits only a boolean use check, even with enrollment closed", async()=>{
  let calls=0,allowed=true;
  const key="k".repeat(48),id=crypto.randomUUID();
  const handler=createHandler({env:{RESEARCH_USE_KEY:key,RESEARCH_ENABLED:"false",
    SUPABASE_URL:"https://example.supabase.co",SUPABASE_SERVICE_ROLE_KEY:"synthetic"},
    fetch:async(url,options)=>{
      calls++;assert.equal(url,"https://example.supabase.co/rest/v1/rpc/qs_research_use_allowed");
      assert.deepEqual(JSON.parse(options.body),{p_extract:id});
      return Response.json(allowed);
    },operations:{record:async()=>{},admit:async()=>{}}});
  const request=(token,value={extract_id:id})=>new Request("https://example.test/qs-service/internal/research-use",{
    method:"POST",headers:{"Content-Type":"application/json",Authorization:"Bearer "+token},body:JSON.stringify(value)});
  assert.equal((await handler(request("wrong"))).status,401);assert.equal(calls,0);
  assert.equal((await handler(request(key,{extract_id:id,enable:true}))).status,400);assert.equal(calls,0);
  assert.deepEqual(await (await handler(request(key))).json(),{allowed:true});
  allowed=false;assert.deepEqual(await (await handler(request(key))).json(),{allowed:false});
  assert.equal(calls,2);
});
