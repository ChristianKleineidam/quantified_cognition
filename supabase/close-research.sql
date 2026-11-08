-- Pair with RESEARCH_ENABLED=false. Existing withdrawal/deletion stays available.
begin;
update qs_governance.protocols set enrollment_open=false where enrollment_open;
update qs_private.ops_state set research_expected_enabled=false where singleton;
commit;
