-- Research authorization checks have their own encrypted operational credentials.
-- No refresh tokens, health measurements or provider account IDs enter this table.
create table qs_private.oura_research_authorizations (
    participant_id uuid primary key references qs_private.participants(id),
    generation uuid not null,
    sealed_token jsonb not null check (octet_length(sealed_token::text)<16384),
    expires_at timestamptz not null,
    receipt_hash text not null check (receipt_hash ~ '^[0-9a-f]{64}$'),
    state text not null default 'paused' check (state in ('active','paused')),
    checked_at timestamptz,
    verified_at timestamptz,
    valid_until timestamptz,
    next_check_at timestamptz not null default now(),
    reason text not null default 'unverified'
        check (reason in ('unverified','verified','expired','unavailable','scope_unavailable'))
);
alter table qs_private.oura_research_authorizations enable row level security;
revoke all on qs_private.oura_research_authorizations from public,anon,authenticated,qs_backup_agent,qs_monitor_reader;
grant select,insert,update,delete on qs_private.oura_research_authorizations to service_role;
alter table qs_private.ops_state add column oura_authorization_monitor_at timestamptz;
alter table qs_private.ops_state add column oura_authorization_monitor_required boolean not null default false;
alter table qs_governance.deletion_cases drop constraint deletion_cases_reason_check;
alter table qs_governance.deletion_cases add constraint deletion_cases_reason_check
    check (reason in ('withdrawal','no_continuing_need','mandatory_deadline','provider_revocation'));

create function qs_governance.needs_oura_authorization(p jsonb) returns boolean
language sql immutable security invoker set search_path='' as $$
    select coalesce(p->'providers' ? 'oura',false) or exists(
        select 1 from jsonb_array_elements_text(coalesce(p->'columns','[]')) with ordinality c(name,pos)
        cross join lateral jsonb_array_elements(coalesce(p->'rows','[]')) r(value)
        where c.name like 'oura_%' and coalesce(r.value->>(c.pos::integer-1),'')<>'');
$$;
-- Avoid repeatedly reading large encrypted payloads during scheduled checks.
alter table qs_private.submissions add column has_oura_research boolean not null default false;
update qs_private.submissions set has_oura_research=qs_governance.needs_oura_authorization(payload);
create function qs_governance.oura_authorized(p_id uuid) returns boolean
language sql stable security invoker set search_path='' as $$
    select exists(select 1 from qs_private.oura_research_authorizations
        where participant_id=p_id and state='active' and valid_until>clock_timestamp()
        and expires_at>clock_timestamp());
$$;
create function qs_governance.guard_oura_research() returns trigger
language plpgsql security invoker set search_path='' as $$
declare person uuid;
begin
    if tg_table_name='submissions' then
        new.has_oura_research:=qs_governance.needs_oura_authorization(new.payload);
        if new.has_oura_research
            and not qs_governance.oura_authorized(new.participant_id) then
            raise exception 'oura_authorization_required' using errcode='42501';
        end if;
    elsif new.state='released' and new.holder<>'recovery' then
        -- release_extract already holds participant locks in this same order.
        for person in select distinct m.participant_id from qs_governance.extract_members m
            join qs_private.submissions s using(participant_id)
            where m.extract_id=new.id and s.has_oura_research
            order by m.participant_id loop
            if not qs_governance.oura_authorized(person) then
                raise exception 'oura_authorization_required' using errcode='42501';
            end if;
        end loop;
    end if;
    return new;
end;
$$;
create trigger oura_research_upload before insert or update of payload,has_oura_research on qs_private.submissions
    for each row execute function qs_governance.guard_oura_research();
create trigger oura_research_release before update of state on qs_governance.extracts
    for each row when (new.state='released') execute function qs_governance.guard_oura_research();

create function public.qs_oura_authorization_bind(p_id uuid,p_hash text,p_generation uuid,
    p_token jsonb,p_expires timestamptz,p_receipt_hash text,p_allow_create boolean) returns void
language plpgsql security invoker set search_path='' as $$
declare owner qs_private.participants; previous qs_private.oura_research_authorizations;
begin
    select * into owner from qs_private.participants where id=p_id for update;
    if not found or owner.deleted_at is not null or owner.capability_hash is distinct from p_hash
        then raise exception 'unauthorized' using errcode='28000'; end if;
    if not exists(select 1 from qs_governance.enrollments e join qs_governance.protocols p
        on p.version=e.protocol_version where e.participant_id=p_id and e.withdrawn_at is null
        and e.superseded_at is null and p.approved and p.oura_permitted
        and p.bundle->>'oura_authorization_monitoring'='access-token-v1') then
        raise exception 'oura_monitoring_consent_required' using errcode='42501'; end if;
    select * into previous from qs_private.oura_research_authorizations where participant_id=p_id for update;
    if previous.participant_id is null and not p_allow_create then
        raise exception 'research_closed' using errcode='42501'; end if;
    if p_receipt_hash !~ '^[0-9a-f]{64}$' or p_receipt_hash is null
        or p_expires is null or p_expires<=now() or p_expires>now()+interval '366 days'
        or p_generation is null or p_token is null or octet_length(p_token::text)>=16384
        or (previous.participant_id is not null and previous.receipt_hash<>p_receipt_hash) then
        raise exception 'invalid authorization binding'; end if;
    insert into qs_private.oura_research_authorizations(participant_id,generation,sealed_token,expires_at,receipt_hash)
        values(p_id,p_generation,p_token,p_expires,p_receipt_hash)
        on conflict(participant_id) do update set generation=excluded.generation,
            sealed_token=excluded.sealed_token,expires_at=excluded.expires_at,state='paused',
            valid_until=null,next_check_at=now(),reason='unverified';
    update qs_private.ops_state set oura_authorization_monitor_required=true where singleton;
end;
$$;
create function public.qs_oura_authorization_claim() returns setof jsonb
language plpgsql security invoker set search_path='' as $$
declare item record;
begin
    update qs_private.ops_state set oura_authorization_monitor_at=clock_timestamp(),
        oura_authorization_monitor_required=exists(select 1 from qs_private.oura_research_authorizations) where singleton;
    for item in select * from qs_private.oura_research_authorizations where next_check_at<=now()
        order by next_check_at,participant_id limit 100 for update skip locked loop
        update qs_private.oura_research_authorizations set next_check_at=now()+interval '2 minutes'
            where participant_id=item.participant_id;
        return next jsonb_build_object('id',item.participant_id,'generation',item.generation,
            'token',item.sealed_token,'expires_at',item.expires_at);
    end loop;
end;
$$;
create function public.qs_oura_authorization_result(p_id uuid,p_generation uuid,p_result text) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare owner qs_private.participants; item qs_private.oura_research_authorizations;
begin
    select * into owner from qs_private.participants where id=p_id for update;
    select * into item from qs_private.oura_research_authorizations where participant_id=p_id for update;
    if owner.deleted_at is not null or item.participant_id is null or item.generation<>p_generation then
        return jsonb_build_object('state','superseded'); end if;
    if p_result='revoked' and item.expires_at>clock_timestamp()+interval '2 minutes' then
        -- A cached analysis cannot separate Oura fields inside an encrypted package.
        -- Withdraw the complete participant contribution through the existing lifecycle.
        perform public.qs_delete(p_id,owner.capability_hash);
        update qs_governance.deletion_cases set receipt_hash=item.receipt_hash,reason='provider_revocation'
            where participant_id=p_id;
        update qs_governance.extract_members set delete_by=least(delete_by,
            coalesce(item.verified_at,now())+interval '72 hours')
            where participant_id=p_id and confirmed_at is null;
        delete from qs_private.oura_research_authorizations where participant_id=p_id;
        return jsonb_build_object('state','deleted');
    end if;
    if p_result not in ('verified','expired','unavailable','scope_unavailable','revoked') or p_result is null then
        raise exception 'invalid authorization result'; end if;
    if item.expires_at<=clock_timestamp()+interval '2 minutes' then p_result:='expired'; end if;
    if p_result='revoked' then p_result:='unavailable'; end if;
    update qs_private.oura_research_authorizations set
        state=case when p_result='verified' then 'active' else 'paused' end,
        reason=p_result,checked_at=clock_timestamp(),
        verified_at=case when p_result='verified' then clock_timestamp() else verified_at end,
        sealed_token=case when p_result='expired' then '{}'::jsonb else sealed_token end,
        valid_until=case when p_result='verified' then least(expires_at,clock_timestamp()+interval '10 minutes') else null end,
        next_check_at=clock_timestamp()+interval '5 minutes' where participant_id=p_id;
    if p_result<>'verified' then
        update qs_governance.extracts e set state='held' where state in ('registered','released')
            and holder<>'recovery' and exists(select 1 from qs_governance.extract_members m
                where m.extract_id=e.id and m.participant_id=p_id);
    end if;
    return jsonb_build_object('state',case when p_result='verified' then 'active' else 'paused' end);
end;
$$;
create function qs_governance.oura_cleanup_credentials() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
    if new.deleted_at is not null then
        delete from qs_private.oura_research_authorizations where participant_id=new.id;
    end if;
    return new;
end;
$$;
create trigger oura_cleanup after update of deleted_at on qs_private.participants
    for each row when (new.deleted_at is not null) execute function qs_governance.oura_cleanup_credentials();

-- Current copies require a current use check, including after local decryption.
create function qs_governance.check_extract_use(p_extract uuid) returns boolean
language plpgsql security invoker set search_path='' as $$
begin
    if not exists(select 1 from qs_governance.extracts where id=p_extract and state='released' and holder<>'recovery'
        and access_until>clock_timestamp() and review_due_at>clock_timestamp()) then return false; end if;
    if exists(select 1 from qs_governance.extracts e join qs_governance.recipients c on c.id=e.recipient_id
        where e.id=p_extract and (not c.approved or c.access_until<=clock_timestamp())) then return false; end if;
    if exists(select 1 from qs_governance.extract_members m where m.extract_id=p_extract and not exists(
        select 1 from qs_governance.enrollments e join qs_governance.protocols p on p.version=e.protocol_version
        where e.participant_id=m.participant_id and e.withdrawn_at is null and e.superseded_at is null and p.approved
        and (p.oura_permitted or not exists(select 1 from qs_private.submissions s
            where s.participant_id=m.participant_id and s.has_oura_research)))) then return false; end if;
    if exists(select 1 from qs_governance.extract_members m where m.extract_id=p_extract
        and not exists(select 1 from qs_governance.retention r where r.participant_id=m.participant_id))
        then return false; end if;
    return not exists(select 1 from qs_governance.extract_members m
        join qs_private.participants p on p.id=m.participant_id
        join qs_governance.retention r on r.participant_id=m.participant_id
        where m.extract_id=p_extract and (p.deleted_at is not null or m.deletion_requested_at is not null
        or r.state<>'active' or r.review_due_at<=clock_timestamp() or r.mandatory_delete_at<=clock_timestamp()
        or exists(select 1 from qs_private.submissions s where s.participant_id=m.participant_id
            and s.has_oura_research and not qs_governance.oura_authorized(m.participant_id))));
end;
$$;
alter function qs_governance.maintain_research() rename to maintain_research_before_oura;
create function qs_governance.maintain_research() returns void
language plpgsql security invoker set search_path='' as $$
begin
    if not pg_try_advisory_xact_lock(72843108) then return; end if;
    perform qs_governance.maintain_research_before_oura();
    update qs_governance.extracts e set state='held' where state in ('registered','released')
        and holder<>'recovery' and exists(select 1 from qs_governance.extract_members m
            join qs_private.submissions s using(participant_id) where m.extract_id=e.id
            and s.has_oura_research and not qs_governance.oura_authorized(m.participant_id));
    perform qs_private.set_alert('oura_research_authorization_paused',exists(
        select 1 from qs_private.oura_research_authorizations where state<>'active' or valid_until<=now()));
    perform qs_private.set_alert('oura_research_authorization_monitor_stale',exists(
        select 1 from qs_private.oura_research_authorizations) and coalesce((
        select oura_authorization_monitor_at<now()-interval '10 minutes' from qs_private.ops_state where singleton),true));
end;
$$;
revoke all on function qs_governance.needs_oura_authorization(jsonb),qs_governance.oura_authorized(uuid),
    qs_governance.guard_oura_research(),qs_governance.oura_cleanup_credentials(),qs_governance.check_extract_use(uuid),
    qs_governance.maintain_research(),qs_governance.maintain_research_before_oura(),
    public.qs_oura_authorization_bind(uuid,text,uuid,jsonb,timestamptz,text,boolean),
    public.qs_oura_authorization_claim(),public.qs_oura_authorization_result(uuid,uuid,text)
    from public,anon,authenticated,service_role,qs_backup_agent,qs_monitor_reader;
grant execute on function qs_governance.needs_oura_authorization(jsonb),qs_governance.oura_authorized(uuid),
    qs_governance.guard_oura_research(),qs_governance.oura_cleanup_credentials(),
    public.qs_oura_authorization_bind(uuid,text,uuid,jsonb,timestamptz,text,boolean),
    public.qs_oura_authorization_claim(),public.qs_oura_authorization_result(uuid,uuid,text) to service_role;
