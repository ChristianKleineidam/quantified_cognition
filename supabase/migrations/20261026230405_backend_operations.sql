-- Shared, bounded operational state. No IP addresses, tokens or measurements.
create table qs_private.quota_policy (
    family text primary key check (family in ('oauth','register','submit','delete')),
    global_minute integer not null check (global_minute > 0),
    global_day integer not null check (global_day > 0),
    actor_minute integer not null check (actor_minute > 0),
    actor_day integer not null check (actor_day > 0)
);
insert into qs_private.quota_policy values
    ('oauth',60,2000,10,100), ('register',10,100,2,4),
    ('submit',20,200,3,12), ('delete',120,6000,10,100);
create table qs_private.quota_buckets (
    family text not null references qs_private.quota_policy(family),
    subject text not null check (subject = 'global' or subject ~ '^[0-9a-f]{64}$'),
    window_seconds integer not null check (window_seconds in (60,86400)),
    window_start timestamptz not null,
    used integer not null check (used >= 0),
    primary key (family,subject,window_seconds,window_start)
);
create index quota_buckets_expiry on qs_private.quota_buckets(window_start);
create table qs_private.ops_metrics (
    minute timestamptz not null,
    route text not null check (route in ('oura_oauth','google_oauth','register','submit','delete','quota_oauth','quota_register','quota_submit','quota_delete')),
    status integer not null check (status between 100 and 599),
    requests bigint not null default 0,
    duration_ms bigint not null default 0,
    max_duration_ms integer not null default 0,
    primary key (minute,route,status)
);
create table qs_private.ops_alerts (
    code text primary key,
    active boolean not null,
    first_seen timestamptz not null default now(),
    last_seen timestamptz not null default now()
);
create table qs_private.ops_state (
    singleton boolean primary key default true check (singleton),
    maintenance_at timestamptz,
    probe_id bigint,
    probe_checked_at timestamptz,
    probe_ok boolean
);
insert into qs_private.ops_state(singleton) values (true);
create table qs_private.storage_budget (
    singleton boolean primary key default true check (singleton),
    identity_limit integer not null default 10000 check (identity_limit > 0),
    submission_limit integer not null default 5000 check (submission_limit > 0),
    byte_limit bigint not null default 268435456 check (byte_limit > 0),
    participant_submission_limit integer not null default 12 check (participant_submission_limit > 0),
    participant_byte_limit bigint not null default 67108864 check (participant_byte_limit > 0),
    daily_submission_limit integer not null default 100 check (daily_submission_limit > 0),
    daily_byte_limit bigint not null default 134217728 check (daily_byte_limit > 0),
    identities integer not null default 0 check (identities >= 0),
    submissions integer not null default 0 check (submissions >= 0),
    payload_bytes bigint not null default 0 check (payload_bytes >= 0),
    usage_day date not null default (now() at time zone 'UTC')::date,
    daily_submissions integer not null default 0,
    daily_bytes bigint not null default 0
);
alter table qs_private.submissions add column payload_bytes bigint;
update qs_private.submissions set payload_bytes = octet_length(payload::text);
alter table qs_private.submissions alter column payload_bytes set not null;
alter table qs_private.submissions add check (payload_bytes > 0);
insert into qs_private.storage_budget(singleton,identities,submissions,payload_bytes)
select true,(select count(*) from qs_private.participants),count(*),coalesce(sum(payload_bytes),0)
from qs_private.submissions;

alter table qs_private.quota_policy enable row level security;
alter table qs_private.quota_buckets enable row level security;
alter table qs_private.ops_metrics enable row level security;
alter table qs_private.ops_alerts enable row level security;
alter table qs_private.ops_state enable row level security;
alter table qs_private.storage_budget enable row level security;
revoke all on all tables in schema qs_private from public,anon,authenticated;
grant select,insert,update,delete on all tables in schema qs_private to service_role;

create function public.qs_ops_record(p_route text,p_status integer,p_duration_ms integer)
returns void language plpgsql security invoker set search_path = '' as $$
begin
    if p_route not in ('oura_oauth','google_oauth','register','submit','delete')
        or p_status not between 100 and 599 or p_duration_ms not between 0 and 300000
        then raise exception 'invalid metric'; end if;
    insert into qs_private.ops_metrics(minute,route,status,requests,duration_ms,max_duration_ms)
        values (date_trunc('minute',now()),p_route,p_status,1,p_duration_ms,p_duration_ms)
    on conflict (minute,route,status) do update set
        requests = qs_private.ops_metrics.requests + 1,
        duration_ms = qs_private.ops_metrics.duration_ms + excluded.duration_ms,
        max_duration_ms = greatest(qs_private.ops_metrics.max_duration_ms,excluded.max_duration_ms);
end;
$$;

create function public.qs_admit(p_family text,p_subject text) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare policy qs_private.quota_policy; seconds integer; start_at timestamptz;
    v_subject text; used_count integer; limit_count integer; retry integer := 0;
begin
    if p_subject is null or p_subject !~ '^[0-9a-f]{64}$' then raise exception 'invalid subject'; end if;
    -- A single policy-row lock serializes checks and increments across instances.
    -- No bucket is created for a denied random actor once the global budget is full.
    select * into policy from qs_private.quota_policy where family=p_family for update;
    if not found then raise exception 'invalid family'; end if;
    foreach v_subject in array array['global',p_subject] loop
        foreach seconds in array array[60,86400] loop
            start_at := to_timestamp(floor(extract(epoch from now()) / seconds) * seconds);
            limit_count := case when v_subject='global' then
                case when seconds=60 then policy.global_minute else policy.global_day end
                else case when seconds=60 then policy.actor_minute else policy.actor_day end end;
            select used into used_count from qs_private.quota_buckets b where b.family=p_family
                and b.subject=v_subject and b.window_seconds=seconds and b.window_start=start_at;
            if coalesce(used_count,0) >= limit_count then
                retry := greatest(retry,ceil(extract(epoch from start_at + make_interval(secs=>seconds) - now()))::integer);
            end if;
        end loop;
        exit when retry > 0;
    end loop;
    if retry > 0 then
        insert into qs_private.ops_metrics(minute,route,status,requests)
            values(date_trunc('minute',now()),'quota_' || p_family,429,1)
            on conflict (minute,route,status) do update set requests=qs_private.ops_metrics.requests+1;
        return jsonb_build_object('allowed',false,'retry_after',greatest(1,retry));
    end if;
    foreach v_subject in array array['global',p_subject] loop
        foreach seconds in array array[60,86400] loop
            start_at := to_timestamp(floor(extract(epoch from now()) / seconds) * seconds);
            insert into qs_private.quota_buckets values(p_family,v_subject,seconds,start_at,1)
                on conflict (family,subject,window_seconds,window_start) do update
                set used=qs_private.quota_buckets.used+1;
        end loop;
    end loop;
    return jsonb_build_object('allowed',true,'retry_after',0);
end;
$$;

create or replace function public.qs_register(p_id uuid,p_hash text) returns boolean
language plpgsql security invoker set search_path = '' as $$
declare owner qs_private.participants; inserted integer; budget qs_private.storage_budget;
begin
    if p_hash is null or p_hash !~ '^[0-9a-f]{64}$' then raise exception 'invalid credential'; end if;
    insert into qs_private.participants(id,capability_hash) values(p_id,p_hash) on conflict(id) do nothing;
    get diagnostics inserted = row_count;
    select * into owner from qs_private.participants where id=p_id for update;
    if inserted=1 then
        select * into budget from qs_private.storage_budget where singleton for update;
        if budget.identities >= budget.identity_limit then raise exception 'quota_exceeded'; end if;
        update qs_private.storage_budget set identities=identities+1 where singleton;
    end if;
    return owner.deleted_at is null and owner.capability_hash=p_hash;
end;
$$;

create or replace function public.qs_submit(p_id uuid,p_hash text,p_submission uuid,p_payload jsonb,p_payload_hash text)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare owner qs_private.participants; previous qs_private.submissions; budget qs_private.storage_budget;
    bytes bigint := octet_length(p_payload::text); owner_count integer; owner_bytes bigint;
begin
    -- All mutations use participant -> shared budget lock order.
    select * into owner from qs_private.participants where id=p_id for update;
    if not found or owner.deleted_at is not null or owner.capability_hash is distinct from p_hash
        then raise exception 'unauthorized' using errcode='28000'; end if;
    select * into previous from qs_private.submissions where id=p_submission;
    if found then
        if previous.participant_id<>p_id or previous.payload_hash<>p_payload_hash
            then raise exception 'submission conflict' using errcode='23505'; end if;
        return jsonb_build_object('submission_id',previous.id,'received_at',previous.created_at);
    end if;
    if bytes is null or bytes > 8388608 then raise exception 'quota_exceeded'; end if;
    select count(*),coalesce(sum(payload_bytes),0) into owner_count,owner_bytes
        from qs_private.submissions where participant_id=p_id;
    select * into budget from qs_private.storage_budget where singleton for update;
    if budget.usage_day <> (now() at time zone 'UTC')::date then
        update qs_private.storage_budget set usage_day=(now() at time zone 'UTC')::date,
            daily_submissions=0,daily_bytes=0 where singleton returning * into budget;
    end if;
    if owner_count >= budget.participant_submission_limit or owner_bytes+bytes > budget.participant_byte_limit
        or budget.submissions >= budget.submission_limit or budget.payload_bytes+bytes > budget.byte_limit
        or budget.daily_submissions >= budget.daily_submission_limit or budget.daily_bytes+bytes > budget.daily_byte_limit
        then raise exception 'quota_exceeded'; end if;
    insert into qs_private.submissions(id,participant_id,payload,payload_hash,payload_bytes)
        values(p_submission,p_id,p_payload,p_payload_hash,bytes) returning * into previous;
    update qs_private.storage_budget set submissions=submissions+1,payload_bytes=payload_bytes+bytes,
        daily_submissions=daily_submissions+1,daily_bytes=daily_bytes+bytes where singleton;
    return jsonb_build_object('submission_id',previous.id,'received_at',previous.created_at);
end;
$$;

create or replace function public.qs_delete(p_id uuid,p_hash text) returns boolean
language plpgsql security invoker set search_path = '' as $$
declare owner qs_private.participants; removed integer; bytes bigint;
begin
    select * into owner from qs_private.participants where id=p_id for update;
    if not found or owner.deleted_at is not null then return true; end if;
    if owner.capability_hash is distinct from p_hash then return false; end if;
    perform 1 from qs_private.storage_budget where singleton for update;
    select count(*),coalesce(sum(payload_bytes),0) into removed,bytes
        from qs_private.submissions where participant_id=p_id;
    delete from qs_private.submissions where participant_id=p_id;
    update qs_private.storage_budget set submissions=submissions-removed,payload_bytes=payload_bytes-bytes where singleton;
    update qs_private.participants set capability_hash=null,deleted_at=now() where id=p_id;
    -- Daily admission usage and identity tombstones are not refunded by deletion.
    return true;
end;
$$;

create function qs_private.set_alert(p_code text,p_active boolean) returns void
language sql security invoker set search_path = '' as $$
    insert into qs_private.ops_alerts(code,active) values(p_code,p_active)
    on conflict(code) do update set active=excluded.active,last_seen=now(),
        first_seen=case when not qs_private.ops_alerts.active and excluded.active then now() else qs_private.ops_alerts.first_seen end;
$$;
create function qs_private.maintain_operations() returns void
language plpgsql security invoker set search_path = '' as $$
declare budget qs_private.storage_budget; failures bigint; total bigint;
begin
    delete from qs_private.quota_buckets where window_start < now()-interval '2 days';
    delete from qs_private.ops_metrics where minute < now()-interval '7 days';
    delete from qs_private.ops_alerts where not active and last_seen < now()-interval '30 days';
    select * into budget from qs_private.storage_budget where singleton;
    perform qs_private.set_alert('storage_80_percent',budget.payload_bytes >= budget.byte_limit*0.8
        or budget.submissions >= budget.submission_limit*0.8 or budget.identities >= budget.identity_limit*0.8);
    perform qs_private.set_alert('daily_storage_80_percent',budget.usage_day=(now() at time zone 'UTC')::date
        and (budget.daily_bytes >= budget.daily_byte_limit*0.8 or budget.daily_submissions >= budget.daily_submission_limit*0.8));
    perform qs_private.set_alert('request_budget_80_percent',exists(
        select 1 from qs_private.quota_buckets b join qs_private.quota_policy p using(family)
        where b.subject='global' and b.window_start+make_interval(secs=>b.window_seconds)>now()
        and b.used >= 0.8 * case when b.window_seconds=60 then p.global_minute else p.global_day end));
    select coalesce(sum(requests) filter(where status>=500),0),coalesce(sum(requests),0)
        into failures,total from qs_private.ops_metrics where minute>now()-interval '15 minutes';
    perform qs_private.set_alert('service_errors',failures>=5 and failures::numeric/greatest(total,1)>=0.1);
    perform qs_private.set_alert('slow_requests',exists(select 1 from qs_private.ops_metrics
        where minute>now()-interval '15 minutes' and max_duration_ms>=20000));
    update qs_private.ops_state set maintenance_at=now() where singleton;
end;
$$;

revoke all on function public.qs_admit(text,text),public.qs_ops_record(text,integer,integer)
    from public,anon,authenticated;
grant execute on function public.qs_admit(text,text),public.qs_ops_record(text,integer,integer) to service_role;
revoke all on function qs_private.set_alert(text,boolean),qs_private.maintain_operations()
    from public,anon,authenticated;
grant execute on function qs_private.set_alert(text,boolean),qs_private.maintain_operations() to service_role;
