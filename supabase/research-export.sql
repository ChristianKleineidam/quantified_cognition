-- Register an extract and set qs.extract_id as described in RESEARCH_CONTROLS.md.
-- Releases encrypted packages with consent provenance. Decrypt and select the
-- latest daily snapshots locally with tools/research_crypto.py. The private key
-- never belongs on the server. Every release stays inventoried for withdrawal.
select record
from qs_governance.release_extract(current_setting('qs.extract_id')::uuid) as record;
