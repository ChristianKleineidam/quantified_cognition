-- Independent operations use dedicated, restricted Postgres logins. Credentials
-- are provisioned outside migrations; no login is enabled by this file.
do $roles$
begin
    if not exists(select 1 from pg_roles where rolname='qs_monitor_reader') then
        create role qs_monitor_reader nologin noinherit;
    end if;
    if not exists(select 1 from pg_roles where rolname='qs_backup_agent') then
        create role qs_backup_agent nologin noinherit;
    end if;
end;
$roles$;
alter role qs_monitor_reader set default_transaction_read_only=on;
alter role qs_backup_agent set default_transaction_read_only=on;
alter role qs_monitor_reader set statement_timeout='20s';
alter role qs_backup_agent set statement_timeout='120s';
grant usage on schema qs_private to qs_monitor_reader;
grant usage on schema qs_private,qs_governance to qs_backup_agent;

-- A harmless column permits participant row locking without granting changes to
-- identity, credentials or deletion state. Snapshot reads remain read-only;
-- the short inventory transaction takes the same participant locks as withdrawal.
alter table qs_private.participants add column recovery_lock_marker boolean not null default false;
grant update(recovery_lock_marker) on qs_private.participants to qs_backup_agent;
create policy qs_backup_lock on qs_private.participants for update to qs_backup_agent using (true);

do $access$
declare tab record;
begin
    for tab in select schemaname,tablename from pg_tables
        where schemaname in ('qs_private','qs_governance') loop
        execute format('grant select on %I.%I to qs_backup_agent',tab.schemaname,tab.tablename);
        execute format('create policy qs_backup_read on %I.%I for select to qs_backup_agent using (true)',
            tab.schemaname,tab.tablename);
    end loop;
    if to_regclass('supabase_migrations.schema_migrations') is not null then
        grant usage on schema supabase_migrations to qs_backup_agent;
        grant select(version) on supabase_migrations.schema_migrations to qs_backup_agent;
    end if;
end;
$access$;
grant select on qs_private.ops_state,qs_private.ops_alerts,qs_private.storage_budget to qs_monitor_reader;
create policy qs_monitor_read on qs_private.ops_state for select to qs_monitor_reader using (true);
create policy qs_monitor_read on qs_private.ops_alerts for select to qs_monitor_reader using (true);
create policy qs_monitor_read on qs_private.storage_budget for select to qs_monitor_reader using (true);

-- A separate heartbeat distinguishes successful research maintenance from the
-- general operations job. A contended/skipped invocation is not a heartbeat.
alter table qs_private.ops_state add column research_maintenance_at timestamptz;
alter function qs_governance.maintain_research() rename to maintain_research_locked;
create function qs_governance.maintain_research() returns void
language plpgsql security invoker set search_path='' as $$
declare person uuid;
begin
    if not pg_try_advisory_xact_lock(72843108) then return; end if;
    perform qs_governance.maintain_research_locked();
    -- Recovery agents attest only to deletion of their own files. The operator
    -- maintenance role completes participant cases after every copy is confirmed.
    for person in select participant_id from qs_governance.deletion_cases
        where active_deleted_at is not null and completed_at is null loop
        perform qs_governance.finish_deletion(person);
    end loop;
    update qs_private.ops_state set research_maintenance_at=clock_timestamp() where singleton;
end;
$$;
revoke all on function qs_governance.maintain_research()
    from public,anon,authenticated,service_role,qs_monitor_reader,qs_backup_agent;

-- The backup agent may inventory/confirm only its fixed recovery-copy class.
-- It cannot change participant credentials, measurements, consent, recipients,
-- protocol permissions, retention decisions or collaborator confirmations.
grant insert on qs_governance.extracts,qs_governance.extract_members,qs_governance.audit to qs_backup_agent;
grant update(state,released_at) on qs_governance.extracts to qs_backup_agent;
grant update(deletion_requested_at,confirmed_at,confirmation_reference)
    on qs_governance.extract_members to qs_backup_agent;
grant usage on sequence qs_governance.audit_id_seq to qs_backup_agent;
create policy qs_backup_inventory_insert on qs_governance.extracts for insert to qs_backup_agent
    with check(holder='recovery' and recipient_id is null and label='Encrypted recovery copy'
        and purpose='Disaster recovery' and location_reference='silentworker-qc:'||id::text
        and state='released' and released_at=now() and access_until<=now()+interval '7 days');
create policy qs_backup_inventory_update on qs_governance.extracts for update to qs_backup_agent
    using(holder='recovery' and location_reference='silentworker-qc:'||id::text)
    with check(holder='recovery' and location_reference='silentworker-qc:'||id::text
        and state in ('released','deleted'));
create policy qs_backup_member_insert on qs_governance.extract_members for insert to qs_backup_agent
    with check(exists(select 1 from qs_governance.extracts e where e.id=extract_id
        and e.holder='recovery' and e.location_reference='silentworker-qc:'||e.id::text
        and e.created_at=now()));
create policy qs_backup_member_update on qs_governance.extract_members for update to qs_backup_agent
    using(exists(select 1 from qs_governance.extracts e where e.id=extract_id
        and e.holder='recovery' and e.location_reference='silentworker-qc:'||e.id::text));
create policy qs_backup_audit_insert on qs_governance.audit for insert to qs_backup_agent
    with check(action in ('recovery_copy_created','recovery_copy_removed')
        and reference='silentworker-qc:'||object_id::text);

create function qs_governance.begin_recovery_copy(p_id uuid) returns uuid[]
language plpgsql security invoker set search_path='' as $$
declare people uuid[];
begin
    -- Complete the lock pass before collecting the inventory; reads below may
    -- then use exactly these members without holding locks while streaming.
    select coalesce(array_agg(id order by id),'{}'::uuid[]) into people from
        (select id from qs_private.participants where deleted_at is null order by id for share) locked;
    insert into qs_governance.extracts(id,label,holder,purpose,location_reference,
        access_until,review_due_at,state,released_at)
        values(p_id,'Encrypted recovery copy','recovery','Disaster recovery',
            'silentworker-qc:'||p_id::text,now()+interval '7 days',now()+interval '7 days','released',now());
    insert into qs_governance.extract_members(extract_id,participant_id)
        select p_id,unnest(people);
    insert into qs_governance.audit(action,object_id,reference)
        values('recovery_copy_created',p_id,'silentworker-qc:'||p_id::text);
    return people;
end;
$$;
create function qs_governance.confirm_recovery_removed(p_id uuid) returns void
language plpgsql security invoker set search_path='' as $$
begin
    if not exists(select 1 from qs_governance.extracts where id=p_id and holder='recovery'
        and location_reference='silentworker-qc:'||p_id::text) then
        raise exception 'recovery copy unavailable'; end if;
    update qs_governance.extract_members set deletion_requested_at=coalesce(deletion_requested_at,now()),
        confirmed_at=coalesce(confirmed_at,now()),confirmation_reference='backup file removed'
        where extract_id=p_id;
    update qs_governance.extracts set state='deleted' where id=p_id;
    insert into qs_governance.audit(action,object_id,reference)
        values('recovery_copy_removed',p_id,'silentworker-qc:'||p_id::text);
end;
$$;
revoke all on function qs_governance.begin_recovery_copy(uuid),qs_governance.confirm_recovery_removed(uuid)
    from public,anon,authenticated,service_role,qs_monitor_reader;
grant execute on function qs_governance.begin_recovery_copy(uuid),qs_governance.confirm_recovery_removed(uuid)
    to qs_backup_agent;

-- Exceptional rights requests require an operator's prior ownership check.
-- The reference identifies the restricted verification record, never an email,
-- identity document or raw measurement. Participant ID alone is not authority.
create function qs_governance.operator_withdraw(p_id uuid,p_reference text) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare owner qs_private.participants; pending integer;
begin
    if p_reference is null or p_reference !~ '^[A-Za-z0-9][A-Za-z0-9/_:-]{7,119}$' then
        raise exception 'verified request reference required'; end if;
    select * into owner from qs_private.participants where id=p_id for update;
    if not found then raise exception 'participant unavailable'; end if;
    if owner.deleted_at is null then
        perform qs_governance.request_deletion(p_id,'withdrawal');
        perform qs_private.delete_storage(p_id,owner.capability_hash);
        update qs_governance.deletion_cases set active_deleted_at=now() where participant_id=p_id;
    end if;
    perform qs_governance.finish_deletion(p_id);
    insert into qs_governance.audit(action,object_id,reference)
        select 'operator_withdrawal',p_id,p_reference where not exists(
            select 1 from qs_governance.audit where action='operator_withdrawal'
                and object_id=p_id and reference=p_reference);
    select count(*) into pending from qs_governance.extract_members
        where participant_id=p_id and confirmed_at is null;
    return jsonb_build_object('active_storage_deleted',true,'pending_copies',pending,'complete',pending=0);
end;
$$;
revoke all on function qs_governance.operator_withdraw(uuid,text)
    from public,anon,authenticated,service_role,qs_monitor_reader,qs_backup_agent;
