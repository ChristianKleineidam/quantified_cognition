import test from "node:test";
import assert from "node:assert/strict";
import { readFile,readdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { protocol, enroll, payload as withEnrollment } from "./research-fixtures.mjs";

test("Postgres operational limits, atomic accounting, monitoring and privileges",async t=> {
  assert.ok(process.env.PGLITE_TEST_MODULE,"Set PGLITE_TEST_MODULE");
  const {PGlite}=await import(pathToFileURL(process.env.PGLITE_TEST_MODULE).href);
  const db=new PGlite();
  const one=async(sql,params=[]) => (await db.query(sql,params)).rows[0];
  const id="00000000-0000-4000-8000-000000000001", hash="a".repeat(64);
  const admit=subject=>one("select public.qs_admit('submit',$1) result",[subject]);
  const submit=(n,payload={test:"synthetic"})=>one("select public.qs_submit($1,$2,$3,$4,$5) receipt",
    [id,hash,`00000000-0000-4000-8000-${String(n).padStart(12,"0")}`,withEnrollment(id,payload),hash]);
  async function isolated(name,work) {
    await t.test(name,async()=>{ await db.exec("begin"); try {await work();} finally {await db.exec("rollback");} });
  }
  try {
    await db.exec("create role anon; create role authenticated; create role service_role bypassrls;");
    const directory=new URL("../migrations/",import.meta.url);
    for (const name of (await readdir(directory)).filter(n=>n.endsWith(".sql")).sort())
      await db.exec(await readFile(new URL(name,directory),"utf8"));
    await protocol(db);
    await isolated("all private tables have RLS and ordinary clients cannot read or call operational RPCs",async()=>{
      const rows=await db.query("select relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='qs_private' and c.relkind='r'");
      assert.equal(rows.rows.length,8); assert.ok(rows.rows.every(r=>r.relrowsecurity));
      for (const role of ["anon","authenticated"]) {
        await db.exec("savepoint privilege_check; set local role "+role);
        await assert.rejects(one("select public.qs_admit('submit',$1)",[hash]),/permission denied/);
        await db.exec("rollback to privilege_check; savepoint privilege_check; set local role "+role);
        await assert.rejects(one("select public.qs_ops_record('submit',200,1)"),/permission denied/);
        await db.exec("rollback to privilege_check; savepoint privilege_check; set local role "+role);
        await assert.rejects(one("select * from qs_private.ops_metrics"),/permission denied/);
        await db.exec("rollback to privilege_check");
      }
    });
    await isolated("actor and global limits reject without leaking partially charged counters or random buckets",async()=>{
      await db.exec("update qs_private.quota_policy set global_minute=3,global_day=3,actor_minute=2,actor_day=2 where family='submit'");
      assert.equal((await admit(hash)).result.allowed,true);
      assert.equal((await admit(hash)).result.allowed,true);
      const denied=(await admit(hash)).result;
      assert.equal(denied.allowed,false); assert.ok(denied.retry_after>0 && denied.retry_after<=86400);
      assert.equal((await one("select used from qs_private.quota_buckets where family='submit' and subject='global' and window_seconds=60")).used,2);
      assert.equal((await admit("b".repeat(64))).result.allowed,true);
      for(let n=0;n<50;n++) assert.equal((await admit(n.toString(16).padStart(64,"0"))).result.allowed,false);
      assert.equal((await one("select count(*)::int n from qs_private.quota_buckets")).n,6);
      assert.equal((await one("select requests::int n from qs_private.ops_metrics where route='quota_submit'")).n,51);
      assert.equal((await one("select public.qs_admit('delete',$1) result",[hash])).result.allowed,true);
    });
    await isolated("expired windows do not consume current budgets",async()=>{
      await admit(hash);
      await db.exec("update qs_private.quota_buckets set window_start=window_start-interval '3 days'");
      await db.exec("update qs_private.quota_policy set actor_minute=1,global_minute=1 where family='submit'");
      assert.equal((await admit(hash)).result.allowed,true);
      await db.exec("select qs_private.maintain_operations()");
      assert.equal((await one("select count(*)::int n from qs_private.quota_buckets")).n,4);
      assert.equal((await one("select active from qs_private.ops_alerts where code='request_budget_80_percent'")).active,true);
    });
    await isolated("concurrent callers share one global allowance",async()=>{
      await db.exec("update qs_private.quota_policy set global_minute=4,global_day=4 where family='submit'");
      // Concurrent client requests against real PostgreSQL semantics in PGlite.
      const results=await Promise.all(Array.from({length:30},(_,n)=>admit(n.toString(16).padStart(64,"0"))));
      assert.equal(results.filter(r=>r.result.allowed).length,4);
      assert.equal((await one("select used from qs_private.quota_buckets where subject='global' and window_seconds=60")).used,4);
    });
    await isolated("storage retries are free and deletion frees storage without refunding daily work",async()=>{
      await one("select public.qs_register($1,$2)",[id,hash]);
      await enroll(db,id,hash);
      await db.exec("update qs_private.storage_budget set participant_submission_limit=1,submission_limit=1");
      const first=await submit(10), before=await one("select * from qs_private.storage_budget");
      assert.deepEqual(await submit(10),first);
      assert.deepEqual(await one("select * from qs_private.storage_budget"),before);
      await db.exec("savepoint rejected_upload");
      await assert.rejects(submit(11),/quota_exceeded/); await db.exec("rollback to rejected_upload");
      assert.deepEqual(await one("select * from qs_private.storage_budget"),before);
      await one("select public.qs_delete($1,$2)",[id,hash]);
      const after=await one("select * from qs_private.storage_budget");
      assert.equal(after.submissions,0); assert.equal(Number(after.payload_bytes),0);
      assert.equal(after.daily_submissions,1); assert.equal(after.identities,1);
      assert.equal((await one("select public.qs_register($1,$2) ok",[id,hash])).ok,false);
    });
    await isolated("byte, identity and daily limits roll back failed writes",async()=>{
      await one("select public.qs_register($1,$2)",[id,hash]);
      await enroll(db,id,hash);
      for (const column of ["byte_limit","participant_byte_limit","daily_byte_limit"]) {
        await db.exec("savepoint byte_limit; update qs_private.storage_budget set "+column+"=1");
        await assert.rejects(submit(20),/quota_exceeded/);
        await db.exec("rollback to byte_limit");
      }
      await db.exec("update qs_private.storage_budget set identity_limit=1");
      await db.exec("savepoint identity_limit");
      await assert.rejects(one("select public.qs_register($1,$2)",[crypto.randomUUID(),hash]),/quota_exceeded/);
      await db.exec("rollback to identity_limit");
      assert.equal((await one("select count(*)::int n from qs_private.participants")).n,1);
      await db.exec("update qs_private.storage_budget set daily_submission_limit=1");
      await submit(20); await db.exec("savepoint daily_limit");
      await assert.rejects(submit(21),/quota_exceeded/); await db.exec("rollback to daily_limit");
      await db.exec("update qs_private.storage_budget set usage_day=usage_day-1");
      await submit(21);
      assert.equal((await one("select daily_submissions from qs_private.storage_budget")).daily_submissions,1);
    });
    await isolated("monitor aggregates, error/latency alerts, heartbeat and retention",async()=>{
      await db.exec("select public.qs_ops_record('submit',502,21000) from generate_series(1,5)");
      await db.exec("select public.qs_ops_record('submit',200,10)");
      await db.exec("insert into qs_private.ops_metrics(minute,route,status,requests) values(now()-interval '8 days','delete',200,1)");
      await db.exec("select qs_private.maintain_operations()");
      assert.equal((await one("select requests::int n from qs_private.ops_metrics where status=502")).n,5);
      const alerts=(await db.query("select code from qs_private.ops_alerts where active order by code")).rows.map(r=>r.code);
      assert.deepEqual(alerts,["service_errors","slow_requests"]);
      assert.equal((await one("select count(*)::int n from qs_private.ops_metrics")).n,2);
      assert.ok((await one("select maintenance_at from qs_private.ops_state")).maintenance_at);
    });
  } finally {await db.close();}
});
