-- Separate metadata-only credential for the off-server analysis use guard.
-- Login/password provisioning is outside migration/source control.
do $role$ begin
    if not exists(select 1 from pg_roles where rolname='qs_research_guard') then
        create role qs_research_guard nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls;
    end if;
end; $role$;
alter role qs_research_guard set default_transaction_read_only=on;
grant usage on schema qs_private,qs_governance to qs_research_guard;
grant select(id,deleted_at) on qs_private.participants to qs_research_guard;
grant select(participant_id,has_oura_research) on qs_private.submissions to qs_research_guard;
grant select(participant_id,state,valid_until,expires_at)
    on qs_private.oura_research_authorizations to qs_research_guard;
grant select(id,state,holder,access_until,review_due_at,recipient_id)
    on qs_governance.extracts to qs_research_guard;
grant select(id,approved,access_until) on qs_governance.recipients to qs_research_guard;
grant select(extract_id,participant_id,deletion_requested_at)
    on qs_governance.extract_members to qs_research_guard;
grant select(participant_id,protocol_version,withdrawn_at,superseded_at)
    on qs_governance.enrollments to qs_research_guard;
grant select(version,approved,oura_permitted) on qs_governance.protocols to qs_research_guard;
grant select(participant_id,state,review_due_at,mandatory_delete_at)
    on qs_governance.retention to qs_research_guard;
create policy research_guard_read on qs_private.participants for select to qs_research_guard using(true);
create policy research_guard_read on qs_private.submissions for select to qs_research_guard using(true);
create policy research_guard_read on qs_private.oura_research_authorizations
    for select to qs_research_guard using(true);
create policy research_guard_read on qs_governance.extracts for select to qs_research_guard using(true);
create policy research_guard_read on qs_governance.recipients for select to qs_research_guard using(true);
create policy research_guard_read on qs_governance.extract_members
    for select to qs_research_guard using(true);
create policy research_guard_read on qs_governance.enrollments for select to qs_research_guard using(true);
create policy research_guard_read on qs_governance.protocols for select to qs_research_guard using(true);
create policy research_guard_read on qs_governance.retention for select to qs_research_guard using(true);
grant execute on function qs_governance.check_extract_use(uuid),qs_governance.oura_authorized(uuid)
    to qs_research_guard;
