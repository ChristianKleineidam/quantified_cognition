// Structurally valid opaque envelopes for server admission tests. Real HPKE
// encryption/decryption and tampering are tested by the Python crypto tests.
import schema from '../functions/qs-service/schema.json' with {type:'json'};
export function reviews() {
  return {columns:schema.review_columns,rows:[],context:{
    prediction_basis:"reconstructed_current_fsrs",anki_version:"26.9.3",
    calculation_version:7,parameters_hash:"a".repeat(64),
    generated_at:"2026-11-07T00:00:00Z",deleted_history_status:"synthetic"}};
}
export function envelope(packet, extra={}) {
  const providers=['oura','google_health'].filter(provider=>
    packet.columns.some((name,index)=>name.startsWith(provider+'_')&&packet.rows.some(row=>row[index]!=='')));
  return {envelope_version:1,algorithm:schema.encryption.algorithm,compression:'zlib',key_id:schema.encryption.key_id,
    enrollment_id:packet.enrollment_id,submission_id:packet.submission_id,created_at:packet.created_at,
    metadata:Object.fromEntries(['research_participant_id','sharing_notice_version','sharing_notice_hash']
      .map(name=>[name,packet.metadata[name]])),providers:providers.sort(),row_count:packet.rows.length,
    enc:Buffer.alloc(32,1).toString('base64'),ciphertext:Buffer.alloc(32,2).toString('base64'),...extra};
}
