-- Explicit operator launch only; never run as part of routine migrations.
begin;
do $launch$ begin
    if not exists(select 1 from qs_governance.protocols
        where version='research-enrollment-v14'
        and bundle_hash='aa255542743b21b08500a7ca0e34987194f174fd49df831af75647839d19b442'
        and notice_version='research-sharing-v12'
        and notice_hash='04e9fec3b375b9a127619bc8eabcae197bff14676e7ea4a637901f308a6ef33c'
        and signature_method='explicit_consent') then
        raise exception 'Launch documents do not match the reviewed release';
    end if;
end; $launch$;
update qs_governance.protocols set approved=true,enrollment_open=true,
    oura_permitted=true,google_permitted=false,
    approval_reference='Operator public-release authorization 2026-11-08; LAUNCH.md; Oura tickets 8481236 / 8471927'
    where version='research-enrollment-v14';
update qs_private.ops_state set oura_expected_enabled=true,research_expected_enabled=true
    where singleton;
commit;
