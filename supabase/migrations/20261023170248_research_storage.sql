-- Only the Edge Function's server credential may execute these invoker RPCs.
-- No client can read tables or list another participant's submissions.
create schema qs_private;
revoke all on schema qs_private from public, anon, authenticated;
grant usage on schema qs_private to service_role;

create table qs_private.participants (
    id uuid primary key,
    capability_hash text,
    created_at timestamptz not null default now(),
    deleted_at timestamptz,
    check (capability_hash is null or capability_hash ~ '^[0-9a-f]{64}$')
);
create table qs_private.submissions (
    id uuid primary key,
    participant_id uuid not null references qs_private.participants(id),
    payload jsonb not null,
    payload_hash text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz not null default now(),
    check (pg_column_size(payload) <= 8388608)
);
create index submissions_participant on qs_private.submissions(participant_id);
alter table qs_private.participants enable row level security;
alter table qs_private.submissions enable row level security;
revoke all on all tables in schema qs_private from public, anon, authenticated;
grant select, insert, update, delete on all tables in schema qs_private to service_role;

create function public.qs_register(p_id uuid, p_hash text) returns boolean
language plpgsql security invoker set search_path = '' as $$
declare existing qs_private.participants;
begin
    if p_hash !~ '^[0-9a-f]{64}$' then raise exception 'invalid credential'; end if;
    insert into qs_private.participants(id, capability_hash) values (p_id, p_hash)
        on conflict (id) do nothing;
    select * into existing from qs_private.participants where id = p_id for update;
    return existing.deleted_at is null and existing.capability_hash = p_hash;
end;
$$;

create function public.qs_submit(
    p_id uuid, p_hash text, p_submission uuid, p_payload jsonb, p_payload_hash text
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare owner qs_private.participants; previous qs_private.submissions;
begin
    -- Serialize against deletion: a deleted participant cannot recreate data.
    select * into owner from qs_private.participants where id = p_id for update;
    if not found or owner.deleted_at is not null or owner.capability_hash <> p_hash
        then raise exception 'unauthorized' using errcode = '28000'; end if;
    select * into previous from qs_private.submissions where id = p_submission;
    if found then
        if previous.participant_id <> p_id or previous.payload_hash <> p_payload_hash
            then raise exception 'submission conflict' using errcode = '23505'; end if;
        return jsonb_build_object('submission_id', previous.id, 'received_at', previous.created_at);
    end if;
    insert into qs_private.submissions(id, participant_id, payload, payload_hash)
        values (p_submission, p_id, p_payload, p_payload_hash) returning * into previous;
    return jsonb_build_object('submission_id', previous.id, 'received_at', previous.created_at);
end;
$$;

create function public.qs_delete(p_id uuid, p_hash text) returns boolean
language plpgsql security invoker set search_path = '' as $$
declare owner qs_private.participants;
begin
    select * into owner from qs_private.participants where id = p_id for update;
    if not found or owner.deleted_at is not null then return true; end if;
    if owner.capability_hash <> p_hash then return false; end if;
    delete from qs_private.submissions where participant_id = p_id;
    -- Retain only an unlinked random UUID tombstone, preventing stale requests
    -- from registering the deleted identity again. No credential or data remains.
    update qs_private.participants set capability_hash = null, deleted_at = now() where id = p_id;
    return true;
end;
$$;

revoke all on function public.qs_register(uuid, text) from public, anon, authenticated;
revoke all on function public.qs_submit(uuid, text, uuid, jsonb, text)
    from public, anon, authenticated;
revoke all on function public.qs_delete(uuid, text) from public, anon, authenticated;
grant execute on function public.qs_register(uuid, text) to service_role;
grant execute on function public.qs_submit(uuid, text, uuid, jsonb, text) to service_role;
grant execute on function public.qs_delete(uuid, text) to service_role;
