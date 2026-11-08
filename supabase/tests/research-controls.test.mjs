import test from "node:test";
import assert from "node:assert/strict";
import { readFile,readdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { protocol,enroll,evidence,payload,authorizeOura } from "./research-fixtures.mjs";

test("enrollment, retention review and the lifecycle of every registered research copy",async t=>{
  const {PGlite}=await import(pathToFileURL(process.env.PGLITE_TEST_MODULE).href);
  const db=new PGlite();
  const id="00000000-0000-4000-8000-000000000001", other="00000000-0000-4000-8000-000000000002";
  const hash="a".repeat(64), receiptHash="e".repeat(64);
  const one=async(sql,params=[]) => (await db.query(sql,params)).rows[0];
  const submit=(p=payload(id),owner=id)=>one("select public.qs_submit($1,$2,$3,$4,$5) result",[owner,hash,crypto.randomUUID(),p,hash]);
  async function participant(person=id) {
    await db.query("select public.qs_register($1,$2)",[person,hash]); await enroll(db,person,hash);
  }
  async function extract(persons=[id],holder="operator",recipient=null) {
    const uuid=crypto.randomUUID();
    await db.query("select qs_governance.register_extract($1,'test copy',$2,$3,'measurement validation','synthetic workspace',now()+interval '10 days',$4)",
      [uuid,holder,recipient,persons]);
    return uuid;
  }
  const release=uuid=>db.query("select * from qs_governance.release_extract($1)",[uuid]);
  const deletion=()=>one("select public.qs_delete_request($1,$2,$3) result",[id,hash,receiptHash]);
  const status=()=>one("select public.qs_deletion_status($1,$2) result",[id,receiptHash]);
  async function isolated(name,work) {
    await t.test(name,async()=>{await db.exec("begin");try{await work();}finally{await db.exec("rollback");}});
  }
  async function rejects(sql,params,pattern) {
    await db.exec("savepoint rejected");
    await assert.rejects(db.query(sql,params),pattern);
    await db.exec("rollback to rejected");
  }
  try {
    await db.exec("create role anon;create role authenticated;create role service_role bypassrls;");
    const directory=new URL("../migrations/",import.meta.url);
    for(const name of (await readdir(directory)).filter(n=>n.endsWith(".sql")).sort())
      await db.exec(await readFile(new URL(name,directory),"utf8"));
    await isolated("prepared protocol stays closed; ordinary clients and Edge key cannot approve or export",async()=>{
      assert.equal((await one("select public.qs_enrollment_information() result")).result,null);
      assert.ok((await db.query("select approved,enrollment_open,oura_permitted,google_permitted from qs_governance.protocols")).rows
        .every(p=>!p.approved&&!p.enrollment_open&&!p.oura_permitted&&!p.google_permitted));
      await rejects("update qs_governance.protocols set google_permitted=true where version='research-enrollment-v10'",
        [],/research_v10_no_google_health/);
      assert.ok((await db.query("select relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='qs_governance' and c.relkind='r'")).rows.every(r=>r.relrowsecurity));
      assert.ok((await db.query("select not prosecdef as invoker from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='qs_governance'")).rows.every(r=>r.invoker));
      for(const role of ["anon","authenticated","service_role"]) {
        await db.exec("savepoint role_test; set local role "+role);
        await rejects("update qs_governance.protocols set approved=true",[],/permission denied/);
        await rejects("select qs_governance.release_extract($1)",[crypto.randomUUID()],/permission denied/);
        await rejects("select qs_governance.replay_deletions()",[],/permission denied/);
        await db.exec("rollback to role_test");
      }
    });
    await protocol(db);
    await isolated("sharing metadata and registered capability alone cannot upload",async()=>{
      await db.query("select public.qs_register($1,$2)",[id,hash]);
      await rejects("select public.qs_submit($1,$2,$3,$4,$5)",[id,hash,crypto.randomUUID(),payload(id),hash],/enrollment_required/);
      for(const change of [{adult:false},{own_data:false},{explicit_consent:false},{explicit_consent:null},
        {bundle_hash:"c".repeat(64)},{signature:"extra pseudonym"},{signature_method:"typed_pseudonym"},
        {participant_id:null},{signed_at_client:null},{signed_at_client:"infinity"},
        {choice_event_id:0},{before_first_results:"true"}])
        await rejects("select public.qs_enroll($1,$2,$3)",[id,hash,evidence(id,change)],/invalid consent|enrollment_required/);
      const missing=evidence(id);delete missing.explicit_consent;
      await rejects("select public.qs_enroll($1,$2,$3)",[id,hash,missing],/invalid consent/);
    });
    await isolated("enrollment retries are immutable; consent evidence stays outside measurements",async()=>{
      await db.query("select public.qs_register($1,$2)",[id,hash]);
      const signed=evidence(id);
      const first=await one("select public.qs_enroll($1,$2,$3) result",[id,hash,signed]);
      await rejects("update qs_governance.enrollments set evidence='{}'",[],/immutable/);
      await rejects("update qs_governance.protocols set bundle='{}',bundle_hash=$1",["c".repeat(64)],/immutable/);
      assert.deepEqual(await one("select public.qs_enroll($1,$2,$3) result",[id,hash,signed]),first);
      await rejects("select public.qs_enroll($1,$2,$3)",[id,hash,{...signed,before_first_results:false}],/conflict/);
      await rejects("select public.qs_enroll($1,$2,$3)",[id,hash,evidence(crypto.randomUUID(),{participant_id:other})],/conflict/);
      await submit();
      const stored=(await one("select payload from qs_private.submissions")).payload;
      assert.equal(stored.metadata.research_participant_id,signed.participant_id);
      assert.ok(!("explicit_consent" in stored.metadata));
      assert.ok(!("signature" in (await one("select evidence from qs_governance.enrollments")).evidence));
      const renewed=evidence(crypto.randomUUID(),{participant_id:id});
      await db.query("select public.qs_enroll($1,$2,$3)",[id,hash,renewed]);
      const old=await one("select withdrawn_at,superseded_at from qs_governance.enrollments where id=$1",[id]);
      assert.equal(old.withdrawn_at,null);assert.ok(old.superseded_at);
      await db.exec("update qs_governance.enrollments set purge_after=now()-interval '1 day';select qs_governance.maintain_research()");
      assert.equal((await one("select count(*)::int n from qs_governance.enrollments where id=$1",[id])).n,1);
    });
    await isolated("legacy consent evidence stays immutable and a renewal uses the same participant ID",async()=>{
      await db.query("select public.qs_register($1,$2)",[id,hash]);
      await db.exec(`insert into qs_governance.protocols
        (version,bundle_hash,bundle,notice_version,notice_hash,signature_method)
        values ('legacy-typed','${"d".repeat(64)}','{}','test-v1','${"a".repeat(64)}','typed_pseudonym')`);
      const legacy=evidence(other,{participant_id:id,version:"legacy-typed",bundle_hash:"d".repeat(64),
        signature_method:"typed_pseudonym",signature:"Original synthetic signature"});
      await db.query("insert into qs_governance.enrollments(id,participant_id,public_participant_id,protocol_version,evidence) values($1,$2,$2,'legacy-typed',$3)",[other,id,legacy]);
      await enroll(db,id,hash);
      const saved=await one("select evidence,superseded_at from qs_governance.enrollments where id=$1",[other]);
      assert.deepEqual(saved.evidence,legacy);assert.ok(saved.superseded_at);
      await rejects("update qs_governance.enrollments set evidence=evidence-'signature' where id=$1",[other],/immutable/);
      await rejects("select public.qs_enroll($1,$2,$3)",[id,hash,legacy],/invalid consent/);
    });
    await isolated("upload binding rejects another enrollment, different public identity and revoked protocol",async()=>{
      await participant();await participant(other);
      await rejects("select public.qs_submit($1,$2,$3,$4,$5)",[id,hash,crypto.randomUUID(),{...payload(id),enrollment_id:other},hash],/enrollment_required/);
      const changed=payload(id);changed.metadata.research_participant_id=other;
      await rejects("select public.qs_submit($1,$2,$3,$4,$5)",[id,hash,crypto.randomUUID(),changed,hash],/enrollment_required/);
      await db.exec("update qs_governance.protocols set enrollment_open=false,approved=false");
      await rejects("select public.qs_submit($1,$2,$3,$4,$5)",[id,hash,crypto.randomUUID(),payload(id),hash],/enrollment_required/);
    });
    await isolated("provider permission is enforced for daily fields independently of enrollment",async()=>{
      await participant();await db.exec("update qs_governance.protocols set oura_permitted=false");
      const p=payload(id,{columns:["date","oura_deep_sleep_seconds"],rows:[["2026-10-01","100"]]});
      await rejects("select public.qs_submit($1,$2,$3,$4,$5)",[id,hash,crypto.randomUUID(),p,hash],/provider_permission_required/);
      p.rows[0][1]="";await submit(p);
      await db.exec("update qs_governance.protocols set oura_permitted=true");
      await authorizeOura(db,id,hash);
      p.rows[0][1]="100";await submit(p);
      await db.exec("update qs_governance.protocols set oura_permitted=false");
      const uuid=await extract();
      await rejects("select qs_governance.release_extract($1)",[uuid],/provider permission unavailable/);
    });
    await isolated("research measurements have no age expiry; uploads do not reset annual reviews",async()=>{
      await participant();await submit();
      const before=await one("select * from qs_governance.retention");
      await db.exec("update qs_private.submissions set created_at=now()-interval '50 years'");
      await submit();await db.exec("select qs_governance.maintain_research()");
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,2);
      assert.deepEqual(await one("select * from qs_governance.retention"),before);
    });
    await isolated("overdue review holds use; a documented continuing need can resume it",async()=>{
      await participant();await submit();await db.exec("update qs_governance.retention set review_due_at=now()-interval '1 day'");
      await db.exec("select qs_governance.maintain_research()");
      assert.equal((await one("select state from qs_governance.retention")).state,"held");
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,1);
      await rejects("select public.qs_submit($1,$2,$3,$4,$5)",[id,hash,crypto.randomUUID(),payload(id),hash],/enrollment_required/);
      await db.query("select qs_governance.review_retention($1,true,'ongoing validation','review-2026',now()+interval '1 year')",[id]);
      await submit();
    });
    await isolated("a released copy is inventoried; withdrawal stops exports and waits for actual confirmation",async()=>{
      await participant();await submit();const uuid=await extract();
      assert.equal((await release(uuid)).rows.length,1);
      await rejects("select qs_governance.release_extract($1)",[uuid],/extract unavailable/);
      const deleted=(await deletion()).result;
      assert.equal(deleted.active_storage_deleted,true);assert.equal(deleted.complete,false);assert.equal(deleted.pending_copies,1);
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,0);
      assert.deepEqual((await deletion()).result,deleted); // lost DELETE response
      await rejects("select public.qs_deletion_status($1,$2)",[id,hash],/unauthorized/);
      await rejects("select qs_governance.confirm_copy_deletion($1,$2,'')",[uuid,id],/evidence required/);
      await db.query("select qs_governance.confirm_copy_deletion($1,$2,'verified workspace removal')",[uuid,id]);
      assert.equal((await status()).result.complete,true);
      assert.equal((await one("select public.qs_register($1,$2) ok",[id,hash])).ok,false);
      assert.equal((await one("select capability_hash from qs_private.participants where id=$1",[id])).capability_hash,null);
    });
    await isolated("copy review holds use and can be renewed without deleting an older dataset",async()=>{
      await participant();await submit();const uuid=await extract();await release(uuid);
      await db.exec("update qs_governance.extracts set review_due_at=now()-interval '1 day';select qs_governance.maintain_research()");
      assert.equal((await one("select state from qs_governance.extracts")).state,"held");
      assert.equal((await one("select count(*)::int n from qs_governance.extract_members where deletion_requested_at is not null")).n,0);
      await db.query("select qs_governance.review_extract($1,'continuing validation','documented copy review',now()+interval '90 days')",[uuid]);
      assert.equal((await one("select state from qs_governance.extracts")).state,"released");
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,1);
    });
    await isolated("withdrawal before release cancels an unused manifest without inventing a remote copy",async()=>{
      await participant();await submit();const uuid=await extract();
      assert.equal((await deletion()).result.complete,true);
      await rejects("select qs_governance.release_extract($1)",[uuid],/participant unavailable|extract unavailable/);
    });
    await isolated("controlled collaborator requires recorded permission and revoked access creates deletion tasks",async()=>{
      await participant();await submit();const recipient=crypto.randomUUID();
      await db.query(`insert into qs_governance.recipients values
        ($1,'Synthetic university','Test operator','DE','controlled_collaborator','validation','agreement-1',
        'provider-approval-1','EU-only workspace','published partner register',now()+interval '20 days',false)`,[recipient]);
      await db.exec("savepoint unavailable");
      await assert.rejects(extract([id],"collaborator",recipient),/approved recipient/);
      await db.exec("rollback to unavailable;update qs_governance.recipients set approved=true");
      const uuid=await extract([id],"collaborator",recipient);await release(uuid);
      await db.exec("update qs_governance.recipients set approved=false;select qs_governance.maintain_research()");
      assert.equal((await one("select state from qs_governance.extracts where id=$1",[uuid])).state,"deletion_pending");
      assert.ok((await one("select deletion_requested_at from qs_governance.extract_members")).deletion_requested_at);
    });
    await isolated("mandatory deadlines override continuing use; lack of need deletes rather than renews",async()=>{
      await participant();await submit();const uuid=await extract();await release(uuid);
      await db.exec("update qs_governance.retention set mandatory_delete_at=now()-interval '1 hour';select qs_governance.maintain_research()");
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,0);
      assert.equal((await one("select reason from qs_governance.deletion_cases")).reason,"mandatory_deadline");
      assert.equal((await one("select active from qs_private.ops_alerts where code='research_deletion_overdue'")).active,true);
      await participant(other);await submit(payload(other),other);
      await db.query("select qs_governance.review_retention($1,false,null,'no continuing need',null)",[other]);
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,0);
    });
    await isolated("restore replay removes resurrected measurements and keeps accounting consistent",async()=>{
      await participant();await submit();await deletion();
      await db.query("insert into qs_private.submissions(id,participant_id,payload,payload_hash,payload_bytes) values($1,$2,$3,$4,100)",
        [crypto.randomUUID(),id,payload(id),hash]);
      assert.equal((await one("select qs_governance.replay_deletions() n")).n,1);
      assert.equal((await one("select count(*)::int n from qs_private.submissions")).n,0);
      assert.equal((await one("select submissions from qs_private.storage_budget")).submissions,0);
      assert.equal((await status()).result.complete,true);
    });
    await isolated("minimal consent evidence expires separately after completed deletion, not while copies remain",async()=>{
      await participant();await submit();const uuid=await extract();await release(uuid);await deletion();
      await db.exec("update qs_governance.enrollments set purge_after=now()-interval '1 day';select qs_governance.maintain_research()");
      assert.equal((await one("select count(*)::int n from qs_governance.enrollments")).n,1);
      await db.query("select qs_governance.confirm_copy_deletion($1,$2,'deletion confirmed')",[uuid,id]);
      await db.exec("update qs_governance.deletion_cases set completed_at=now()-interval '4 years';select qs_governance.maintain_research()");
      assert.equal((await one("select count(*)::int n from qs_governance.enrollments")).n,0);
      assert.equal((await one("select count(*)::int n from qs_governance.deletion_cases")).n,0);
      assert.equal((await one("select count(*)::int n from qs_governance.extract_members")).n,0);
      assert.equal((await one("select capability_hash from qs_private.participants where id=$1",[id])).capability_hash,null);
    });
  } finally {await db.close();}
});
