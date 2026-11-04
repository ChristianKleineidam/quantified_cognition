// Synthetic protocol, deliberately approved only inside isolated test databases.
export const notice = { version: "test-v1", hash: "a".repeat(64) };
export function evidence(id, extra = {}) {
  return { enrollment_id: id, participant_id: id, version: "synthetic-v1", bundle_hash: "b".repeat(64),
    adult: true, own_data: true, explicit_consent: true, signature_method: "explicit_consent",
    signed_at_client: new Date().toISOString(),
    before_first_results: true, choice_event_id: 1, notice_version: notice.version, notice_hash: notice.hash, ...extra };
}
export function payload(id, extra = {}) {
  return { enrollment_id: id, metadata: {research_participant_id:id,
    sharing_notice_version:notice.version, sharing_notice_hash:notice.hash}, columns:["date"],rows:[["2026-10-01"]], ...extra };
}
export async function protocol(db) {
  await db.exec(`insert into qs_governance.protocols
    (version,bundle_hash,bundle,notice_version,notice_hash,signature_method,approved,approval_reference,oura_permitted,google_permitted,enrollment_open)
    values ('synthetic-v1','${"b".repeat(64)}','{}','${notice.version}','${notice.hash}',
    'explicit_consent',true,'synthetic test approval',true,true,true) on conflict do nothing`);
}
export async function enroll(db, id, hash, extra = {}) {
  await db.query("select public.qs_enroll($1,$2,$3)",[id,hash,evidence(id,extra)]);
}
