-- The desktop receives only a boolean use decision, never database credentials.
create function public.qs_research_use_allowed(p_extract uuid) returns boolean
language sql security invoker set search_path='' as $$
    select qs_governance.check_extract_use(p_extract);
$$;
revoke all on function public.qs_research_use_allowed(uuid) from public,anon,authenticated;
grant execute on function public.qs_research_use_allowed(uuid),
    qs_governance.check_extract_use(uuid),qs_governance.oura_authorized(uuid) to service_role;
grant select(id,approved,access_until) on qs_governance.recipients to service_role;

-- Retire the unused NOLOGIN role introduced during workspace preparation.
do $guard$ begin
    if exists(select 1 from pg_roles where rolname='qs_research_guard' and rolcanlogin)
      or exists(select 1 from pg_shdepend d join pg_roles r on r.oid=d.refobjid
        where r.rolname='qs_research_guard' and d.deptype='o') then
        raise exception 'Unexpected research guard ownership or login';
    end if;
end; $guard$;
drop policy research_guard_read on qs_private.participants;
drop policy research_guard_read on qs_private.submissions;
drop policy research_guard_read on qs_private.oura_research_authorizations;
drop policy research_guard_read on qs_governance.extracts;
drop policy research_guard_read on qs_governance.recipients;
drop policy research_guard_read on qs_governance.extract_members;
drop policy research_guard_read on qs_governance.enrollments;
drop policy research_guard_read on qs_governance.protocols;
drop policy research_guard_read on qs_governance.retention;
revoke all on all tables in schema qs_private,qs_governance from qs_research_guard;
-- Table-level REVOKE does not remove column grants.
revoke select(id,deleted_at) on qs_private.participants from qs_research_guard;
revoke select(participant_id,has_oura_research) on qs_private.submissions from qs_research_guard;
revoke select(participant_id,state,valid_until,expires_at)
    on qs_private.oura_research_authorizations from qs_research_guard;
revoke select(id,state,holder,access_until,review_due_at,recipient_id)
    on qs_governance.extracts from qs_research_guard;
revoke select(id,approved,access_until) on qs_governance.recipients from qs_research_guard;
revoke select(extract_id,participant_id,deletion_requested_at)
    on qs_governance.extract_members from qs_research_guard;
revoke select(participant_id,protocol_version,withdrawn_at,superseded_at)
    on qs_governance.enrollments from qs_research_guard;
revoke select(version,approved,oura_permitted) on qs_governance.protocols from qs_research_guard;
revoke select(participant_id,state,review_due_at,mandatory_delete_at)
    on qs_governance.retention from qs_research_guard;
revoke all on function qs_governance.check_extract_use(uuid),
    qs_governance.oura_authorized(uuid) from qs_research_guard;
revoke all on schema qs_private,qs_governance from qs_research_guard;
drop role qs_research_guard;
