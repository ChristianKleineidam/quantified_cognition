import test from "node:test";
import assert from "node:assert/strict";
import { readFile,readdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { protocol,enroll,payload,authorizeOura } from "./research-fixtures.mjs";

test("offline Oura authorization loss holds research and follows the complete deletion lifecycle",async t=>{
  const {PGlite}=await import(pathToFileURL(process.env.PGLITE_TEST_MODULE).href);
  const db=new PGlite();
  const id="00000000-0000-4000-8000-000000000001",hash="a".repeat(64),receipt="e".repeat(64);
  const one=async(sql,args=[]) => (await db.query(sql,args)).rows[0];
  async function participant() {await db.query("select public.qs_register($1,$2)",[id,hash]);await enroll(db,id,hash);}
  const packet=()=>payload(id,{columns:["date","oura_steps"],rows:[["2026-10-01","100"]]});
  const submit=()=>db.query("select public.qs_submit($1,$2,$3,$4,$5)",[id,hash,crypto.randomUUID(),packet(),hash]);
  async function copy(holder="operator") {
    const copy=crypto.randomUUID();
    await db.query("select qs_governance.register_extract($1,'synthetic',$2,null,'validation','test inventory',now()+interval '1 day',array[$3]::uuid[])",[copy,holder,id]);
    await db.query("select qs_governance.release_extract($1)",[copy]);return copy;
  }
  const result=(generation,state)=>db.query("select public.qs_oura_authorization_result($1,$2,$3)",[id,generation,state]);
  async function rejects(sql,args,pattern) {
    await db.exec("savepoint rejection");await assert.rejects(db.query(sql,args),pattern);await db.exec("rollback to rejection");
  }
  async function isolated(name,work) {
    await t.test(name,async()=>{await db.exec("begin");try{await participant();await work();}finally{await db.exec("rollback");}});
  }
  try {
    await db.exec("create role anon;create role authenticated;create role service_role bypassrls;");
    const directory=new URL("../migrations/",import.meta.url);
    for(const name of (await readdir(directory)).filter(n=>n.endsWith(".sql")).sort())
      await db.exec(await readFile(new URL(name,directory),"utf8"));
    await protocol(db);
    await isolated("an Oura upload needs fresh authorization; Anki-only packages are unaffected",async()=>{
      await rejects("select public.qs_submit($1,$2,$3,$4,$5)",[id,hash,crypto.randomUUID(),packet(),hash],/oura_authorization_required/);
      await db.query("select public.qs_submit($1,$2,$3,$4,$5)",[id,hash,crypto.randomUUID(),payload(id),hash]);
      await authorizeOura(db,id,hash);await submit();
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,2);
    });
    await isolated("outages stop existing-copy use without deleting measurements or assuming withdrawal",async()=>{
      const generation=await authorizeOura(db,id,hash);await submit();const extract=await copy();
      assert.equal((await one("select qs_governance.check_extract_use($1) ok",[extract])).ok,true);
      await db.exec("update qs_governance.protocols set oura_permitted=false");
      assert.equal((await one("select qs_governance.check_extract_use($1) ok",[extract])).ok,false);
      await db.exec("update qs_governance.protocols set oura_permitted=true");
      await result(generation,"unavailable");
      assert.equal((await one("select qs_governance.check_extract_use($1) ok",[extract])).ok,false);
      assert.equal((await one("select state from qs_governance.extracts where id=$1",[extract])).state,"held");
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,1);
      assert.equal((await one("select count(*)::int n from qs_governance.deletion_cases")).n,0);
    });
    await isolated("a stopped scheduler expires authorization and prevents new releases",async()=>{
      await authorizeOura(db,id,hash);await submit();const extract=await copy();
      await db.exec("update qs_private.oura_research_authorizations set valid_until=now()-interval '1 second'");
      assert.equal((await one("select qs_governance.check_extract_use($1) ok",[extract])).ok,false);
      await db.exec("select qs_governance.maintain_research()");
      assert.equal((await one("select state from qs_governance.extracts where id=$1",[extract])).state,"held");
    });
    await isolated("revocation erases active data and tracks every copy until confirmed",async()=>{
      const generation=await authorizeOura(db,id,hash,receipt);await submit();
      const work=await copy(),backup=await copy("recovery");
      assert.equal((await one("select qs_governance.check_extract_use($1) ok",[backup])).ok,false);
      await result(generation,"revoked");
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,0);
      assert.equal((await one("select count(*)::int n from qs_private.oura_research_authorizations")).n,0);
      assert.equal((await one("select count(*)::int n from qs_private.participants where deleted_at is not null and capability_hash is null")).n,1);
      const tasks=(await db.query("select delete_by<=now()+interval '72 hours' timely from qs_governance.extract_members where confirmed_at is null")).rows;
      assert.equal(tasks.length,2);assert.ok(tasks.every(r=>r.timely));
      let status=(await one("select public.qs_deletion_status($1,$2) status",[id,receipt])).status;
      assert.equal(status.active_storage_deleted,true);assert.equal(status.complete,false);
      for(const copy of [work,backup])await db.query("select qs_governance.confirm_copy_deletion($1,$2,'synthetic file removed')",[copy,id]);
      await db.exec("select qs_governance.maintain_research()");
      status=(await one("select public.qs_deletion_status($1,$2) status",[id,receipt])).status;
      assert.equal(status.complete,true);
      await rejects("select public.qs_submit($1,$2,$3,$4,$5)",[id,hash,crypto.randomUUID(),packet(),hash],/unauthorized/);
    });
    await isolated("expiry pauses and a stale failure cannot delete a new binding",async()=>{
      const old=await authorizeOura(db,id,hash);await submit();
      const fresh=await authorizeOura(db,id,hash);await result(old,"revoked");
      assert.equal((await one("select qs_governance.oura_authorized($1) ok",[id])).ok,true);
      await db.exec("update qs_private.oura_research_authorizations set expires_at=now()-interval '1 second'");
      await result(fresh,"revoked");
      assert.equal((await one("select reason from qs_private.oura_research_authorizations")).reason,"expired");
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,1);
    });
    await isolated("credentials and monitor RPCs are inaccessible to ordinary clients and backups",async()=>{
      await authorizeOura(db,id,hash);
      for(const role of ["anon","authenticated","qs_backup_agent","qs_monitor_reader"]) {
        await db.exec("savepoint role_check;set local role "+role);
        await rejects("select * from qs_private.oura_research_authorizations",[],/permission denied/);
        await rejects("select public.qs_oura_authorization_claim()",[],/permission denied/);
        await db.exec("rollback to role_check");
      }
    });
  } finally {await db.close();}
});
