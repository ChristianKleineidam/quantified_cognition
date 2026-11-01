// Real PostgreSQL via PGlite: no Docker, cloud account or personal data needed.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";

test("Postgres migration: RLS/privileges, ownership, deduplication, deletion and stale uploads", async () => {
  const module = process.env.PGLITE_TEST_MODULE;
  assert.ok(module, "Set PGLITE_TEST_MODULE to the installed @electric-sql/pglite/dist/index.js");
  const { PGlite } = await import(pathToFileURL(module).href);
  const db = new PGlite();
  try {
    await db.exec("create role anon; create role authenticated; create role service_role bypassrls;");
    const directory = new URL("../migrations/", import.meta.url);
    for (const name of (await readdir(directory)).filter(n => n.endsWith(".sql")).sort()) {
      await db.exec(await readFile(new URL(name, directory), "utf8"));
    }
    const id = "00000000-0000-4000-8000-000000000001", other = "00000000-0000-4000-8000-000000000002";
    const submission = "00000000-0000-4000-8000-000000000003", hash = "a".repeat(64);
    for (const role of ["anon", "authenticated"]) {
      await db.exec("set role " + role);
      await assert.rejects(db.query("select * from qs_private.submissions"), /permission denied/);
      await assert.rejects(db.query("select public.qs_register($1, $2)", [id, hash]), /permission denied/);
      await assert.rejects(db.query("select public.qs_delete($1, $2)", [id, hash]), /permission denied/);
      await assert.rejects(db.query("select public.qs_submit($1,$2,$3,$4,$5)", [id, hash, submission, {}, hash]), /permission denied/);
      await db.exec("reset role");
    }
    const rls = await db.query("select relrowsecurity from pg_class where relname in ('participants','submissions')");
    assert.ok(rls.rows.every(r => r.relrowsecurity));
    await db.exec("set role service_role");
    assert.equal((await db.query("select public.qs_register($1,$2) ok", [id, hash])).rows[0].ok, true);
    assert.equal((await db.query("select public.qs_register($1,$2) ok", [id, "b".repeat(64)])).rows[0].ok, false);
    await db.query("select public.qs_register($1,$2)", [other, hash]);
    const submit = params => db.query("select public.qs_submit($1,$2,$3,$4,$5) receipt", params);
    const params = [id, hash, submission, { schema_version: 1,
      metadata: { research_participant_id: id }, columns: ["date", "recall_score_pp"],
      rows: [["2026-10-01", "5"]] }, hash];
    const first = (await submit(params)).rows[0].receipt;
    assert.deepEqual((await submit(params)).rows[0].receipt, first);
    await assert.rejects(submit([id, "wrong", submission, {}, hash]), /unauthorized/);
    await assert.rejects(submit([other, hash, submission, {}, hash]), /submission conflict/);
    await assert.rejects(submit([id, hash, submission, {}, "b".repeat(64)]), /submission conflict/);
    assert.equal((await db.query("select count(*)::int n from qs_private.submissions")).rows[0].n, 1);
    const exported = await db.query(await readFile(new URL("../research-export.sql", import.meta.url), "utf8"));
    assert.equal(exported.rows[0].daily_record.date, "2026-10-01");
    assert.equal(exported.rows[0].daily_record.recall_score_pp, "5");
    assert.equal((await db.query("select public.qs_delete($1,$2) ok", [id, "wrong"])).rows[0].ok, false);
    assert.equal((await db.query("select public.qs_delete($1,$2) ok", [id, hash])).rows[0].ok, true);
    assert.equal((await db.query("select count(*)::int n from qs_private.submissions")).rows[0].n, 0);
    await assert.rejects(submit(params), /unauthorized/);
    assert.equal((await db.query("select public.qs_register($1,$2) ok", [id, hash])).rows[0].ok, false);
    assert.equal((await db.query("select public.qs_delete($1,$2) ok", [id, hash])).rows[0].ok, true);
    assert.equal((await db.query("select capability_hash from qs_private.participants where id=$1", [id])).rows[0].capability_hash, null);
  } finally { await db.close(); }
});
