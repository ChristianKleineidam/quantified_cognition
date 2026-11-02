-- Enrollment evidence and research governance are separate from measurement payloads.
-- Everything remains closed: installing this migration approves no protocol/provider.
create schema qs_governance;
revoke all on schema qs_governance from public, anon, authenticated;
grant usage on schema qs_governance to service_role;

create table qs_governance.protocols (
    version text primary key,
    bundle_hash text not null check (bundle_hash ~ '^[0-9a-f]{64}$'),
    bundle jsonb not null,
    notice_version text not null,
    notice_hash text not null check (notice_hash ~ '^[0-9a-f]{64}$'),
    signature_method text not null check (signature_method = 'typed_pseudonym'),
    approved boolean not null default false,
    enrollment_open boolean not null default false,
    approval_reference text,
    oura_permitted boolean not null default false,
    google_permitted boolean not null default false,
    lock_marker boolean not null default false,
    check (not approved or (approval_reference is not null and length(trim(approval_reference)) > 0)),
    check (not enrollment_open or approved)
);
create unique index one_open_protocol on qs_governance.protocols ((true)) where enrollment_open;
-- The immutable bundle is seeded by the companion migration generated from the
-- bundled participant documents. No mutable URL is treated as signed evidence.

create table qs_governance.enrollments (
    id uuid primary key,
    participant_id uuid not null references qs_private.participants(id),
    public_participant_id uuid not null,
    protocol_version text not null references qs_governance.protocols(version),
    evidence jsonb not null check (octet_length(evidence::text) <= 16384),
    received_at timestamptz not null default now(),
    superseded_at timestamptz,
    withdrawn_at timestamptz,
    purge_after timestamptz,
    unique(participant_id, id)
);
create index enrollments_participant on qs_governance.enrollments(participant_id);
create index enrollments_protocol on qs_governance.enrollments(protocol_version);
create index submissions_enrollment on qs_private.submissions ((payload->>'enrollment_id'));
create table qs_governance.retention (
    participant_id uuid primary key references qs_private.participants(id),
    purpose text not null,
    last_review_at timestamptz not null default now(),
    review_due_at timestamptz not null default (now() + interval '1 year'),
    mandatory_delete_at timestamptz,
    state text not null default 'active' check (state in ('active','held','withdrawn')),
    decision_reference text not null
);
create index retention_due on qs_governance.retention(review_due_at) where state <> 'withdrawn';
create index retention_deadline on qs_governance.retention(mandatory_delete_at)
    where state <> 'withdrawn' and mandatory_delete_at is not null;
create table qs_governance.recipients (
    id uuid primary key,
    organization text not null check (length(trim(organization)) > 0),
    responsible_person text not null check (length(trim(responsible_person)) > 0),
    country text not null check (length(trim(country)) > 0),
    controller_role text not null check (controller_role in ('controlled_collaborator','processor')),
    purpose text not null check (length(trim(purpose)) > 0),
    agreement_reference text not null check (length(trim(agreement_reference)) > 0),
    permission_reference text not null check (length(trim(permission_reference)) > 0),
    transfer_reference text not null check (length(trim(transfer_reference)) > 0),
    consent_reference text not null check (length(trim(consent_reference)) > 0),
    access_until timestamptz not null,
    approved boolean not null default false
);
create table qs_governance.extracts (
    id uuid primary key,
    label text not null check (length(trim(label)) > 0),
    holder text not null check (holder in ('operator','workspace','collaborator','recovery')),
    recipient_id uuid references qs_governance.recipients(id),
    purpose text not null check (length(trim(purpose)) > 0),
    location_reference text not null check (length(trim(location_reference)) > 0),
    created_at timestamptz not null default now(),
    access_until timestamptz not null,
    review_due_at timestamptz not null,
    state text not null default 'registered' check (state in ('registered','released','held','deletion_pending','deleted')),
    released_at timestamptz,
    check ((holder = 'collaborator') = (recipient_id is not null)),
    check (access_until > created_at),
    check (holder <> 'recovery' or access_until <= created_at + interval '30 days')
);
create index extracts_recipient on qs_governance.extracts(recipient_id);
create table qs_governance.extract_members (
    extract_id uuid not null references qs_governance.extracts(id),
    participant_id uuid not null references qs_private.participants(id),
    deletion_requested_at timestamptz,
    delete_by timestamptz,
    confirmed_at timestamptz,
    confirmation_reference text,
    primary key(extract_id, participant_id),
    check ((confirmed_at is null) = (confirmation_reference is null)),
    check (confirmation_reference is null or length(trim(confirmation_reference)) > 0)
);
create index extract_members_participant on qs_governance.extract_members(participant_id);
create index extract_members_pending on qs_governance.extract_members(delete_by)
    where deletion_requested_at is not null and confirmed_at is null;
create table qs_governance.deletion_cases (
    participant_id uuid primary key references qs_private.participants(id),
    receipt_hash text check (receipt_hash ~ '^[0-9a-f]{64}$'),
    requested_at timestamptz not null default now(),
    active_deleted_at timestamptz,
    completed_at timestamptz,
    reason text not null check (reason in ('withdrawal','no_continuing_need','mandatory_deadline'))
);
create table qs_governance.audit (
    id bigint generated always as identity primary key,
    occurred_at timestamptz not null default now(),
    actor text not null default session_user,
    action text not null,
    object_id uuid,
    reference text not null
);

create function qs_governance.freeze_protocol() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
    if row(new.version,new.bundle_hash,new.bundle,new.notice_version,new.notice_hash,new.signature_method)
        is distinct from row(old.version,old.bundle_hash,old.bundle,old.notice_version,old.notice_hash,old.signature_method)
        then raise exception 'signed protocol terms are immutable; insert a new version'; end if;
    return new;
end;
$$;
create trigger freeze_protocol before update on qs_governance.protocols
    for each row execute function qs_governance.freeze_protocol();
create function qs_governance.freeze_enrollment() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
    if row(new.id,new.participant_id,new.public_participant_id,new.protocol_version,new.evidence,new.received_at)
        is distinct from row(old.id,old.participant_id,old.public_participant_id,old.protocol_version,old.evidence,old.received_at)
        then raise exception 'signed enrollment evidence is immutable'; end if;
    return new;
end;
$$;
create trigger freeze_enrollment before update on qs_governance.enrollments
    for each row execute function qs_governance.freeze_enrollment();

do $security$
declare tab record;
begin
    for tab in select tablename from pg_tables where schemaname='qs_governance' loop
        execute format('alter table qs_governance.%I enable row level security',tab.tablename);
        execute format('revoke all on qs_governance.%I from public,anon,authenticated',tab.tablename);
    end loop;
end;
$security$;
-- Only enrollment and deletion RPCs use the Edge key. No extract/recipient/audit
-- management endpoint or general-purpose research download is exposed over HTTP.
grant select on qs_governance.protocols to service_role;
-- PostgreSQL row locks require UPDATE on at least one column. The Edge role
-- may lock a protocol without being allowed to approve it or change its terms.
grant update(lock_marker) on qs_governance.protocols to service_role;
grant select,insert,update on qs_governance.enrollments,qs_governance.retention,
    qs_governance.deletion_cases to service_role;
grant select,update on qs_governance.extracts,qs_governance.extract_members to service_role;

create function public.qs_enrollment_information() returns jsonb
language sql security invoker set search_path='' as $$
    select jsonb_build_object('version',version,'bundle_hash',bundle_hash,
        'signature_method',signature_method,'notice_version',notice_version,'notice_hash',notice_hash)
    from qs_governance.protocols where approved and enrollment_open;
$$;
create function public.qs_enroll(p_id uuid,p_hash text,p_evidence jsonb) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare owner qs_private.participants; protocol qs_governance.protocols;
    previous qs_governance.enrollments; signed_id uuid; public_id uuid;
begin
    select * into owner from qs_private.participants where id=p_id for update;
    if not found or owner.deleted_at is not null or owner.capability_hash is distinct from p_hash
        then raise exception 'unauthorized' using errcode='28000'; end if;
    select * into protocol from qs_governance.protocols
        where version=p_evidence->>'version' and approved and enrollment_open for share;
    if not found or protocol.bundle_hash is distinct from p_evidence->>'bundle_hash'
        or protocol.notice_version is distinct from p_evidence->>'notice_version'
        or protocol.notice_hash is distinct from p_evidence->>'notice_hash'
        or protocol.signature_method is distinct from p_evidence->>'signature_method'
        then raise exception 'enrollment_required' using errcode='42501'; end if;
    if p_evidence->'adult' is distinct from 'true'::jsonb
        or p_evidence->'own_data' is distinct from 'true'::jsonb
        or p_evidence->'explicit_consent' is distinct from 'true'::jsonb
        or length(trim(p_evidence->>'signature')) not between 2 and 80
        or p_evidence->>'signature' ~ '[[:cntrl:]]'
        or (p_evidence->>'signed_at_client')::timestamptz > now()+interval '1 day'
        then raise exception 'invalid consent' using errcode='22023'; end if;
    signed_id := (p_evidence->>'enrollment_id')::uuid;
    public_id := (p_evidence->>'participant_id')::uuid;
    if signed_id is null or public_id is null or p_evidence->>'signature' is null
        or p_evidence->>'signed_at_client' is null then raise exception 'invalid consent'; end if;
    select * into previous from qs_governance.enrollments where id=signed_id;
    if found then
        if previous.participant_id <> p_id or previous.evidence <> p_evidence
            then raise exception 'enrollment conflict' using errcode='23505'; end if;
        if previous.withdrawn_at is not null then raise exception 'enrollment_required' using errcode='42501'; end if;
        return jsonb_build_object('enrollment_id',previous.id,'received_at',previous.received_at);
    end if;
    -- Re-signing replaces the active enrollment, but never resets retention review.
    update qs_governance.enrollments set superseded_at=now(),purge_after=now()+interval '3 years'
        where participant_id=p_id and withdrawn_at is null and superseded_at is null;
    insert into qs_governance.enrollments(id,participant_id,public_participant_id,protocol_version,evidence)
        values(signed_id,p_id,public_id,protocol.version,p_evidence) returning * into previous;
    insert into qs_governance.retention(participant_id,purpose,decision_reference)
        values(p_id,'Learning and recall variation; sleep, activity, study patterns and measurement validation',
            'initial enrollment: ' || protocol.version) on conflict(participant_id) do nothing;
    return jsonb_build_object('enrollment_id',previous.id,'received_at',previous.received_at);
end;
$$;

-- Preserve tested quota/accounting code in the unexposed schema. Only the new
-- public wrapper can submit through PostgREST. Renaming does not erase history.
alter function public.qs_submit(uuid,text,uuid,jsonb,text) set schema qs_private;
alter function qs_private.qs_submit(uuid,text,uuid,jsonb,text) rename to store_submission;
alter function public.qs_delete(uuid,text) set schema qs_private;
alter function qs_private.qs_delete(uuid,text) rename to delete_storage;

create function qs_governance.provider_fields_allowed(p_payload jsonb,p_oura boolean,p_google boolean)
returns boolean language sql immutable security invoker set search_path='' as $$
    select not exists(select 1 from jsonb_array_elements_text(p_payload->'columns') with ordinality c(name,pos)
        cross join lateral jsonb_array_elements(p_payload->'rows') r(value)
        where ((c.name like 'oura_%' and not p_oura) or (c.name like 'google_health_%' and not p_google))
            and coalesce(r.value->>(c.pos::integer-1),'') <> '');
$$;

create function public.qs_submit(p_id uuid,p_hash text,p_submission uuid,p_payload jsonb,p_payload_hash text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare owner qs_private.participants; enrollment qs_governance.enrollments;
    protocol qs_governance.protocols; retention qs_governance.retention;
begin
    select * into owner from qs_private.participants where id=p_id for update;
    if not found or owner.deleted_at is not null or owner.capability_hash is distinct from p_hash
        then raise exception 'unauthorized' using errcode='28000'; end if;
    select * into enrollment from qs_governance.enrollments
        where id=(p_payload->>'enrollment_id')::uuid and participant_id=p_id
            and withdrawn_at is null and superseded_at is null;
    if not found then raise exception 'enrollment_required' using errcode='42501'; end if;
    select * into protocol from qs_governance.protocols where version=enrollment.protocol_version for share;
    select * into retention from qs_governance.retention where participant_id=p_id;
    if not found then raise exception 'enrollment_required' using errcode='42501'; end if;
    if not protocol.approved or retention.state <> 'active' or retention.review_due_at <= now()
        or retention.mandatory_delete_at <= now()
        or p_payload->'metadata'->>'research_participant_id' is distinct from enrollment.public_participant_id::text
        or p_payload->'metadata'->>'sharing_notice_hash' is distinct from protocol.notice_hash
        or p_payload->'metadata'->>'sharing_notice_version' is distinct from protocol.notice_version
        then raise exception 'enrollment_required' using errcode='42501'; end if;
    if not qs_governance.provider_fields_allowed(p_payload,protocol.oura_permitted,protocol.google_permitted)
        then raise exception 'provider_permission_required' using errcode='42501'; end if;
    return qs_private.store_submission(p_id,p_hash,p_submission,p_payload,p_payload_hash);
end;
$$;

create function qs_governance.request_deletion(p_id uuid,p_reason text) returns void
language plpgsql security invoker set search_path='' as $$
begin
    -- Caller locks the participant before all governance/storage work.
    insert into qs_governance.deletion_cases(participant_id,reason) values(p_id,p_reason)
        on conflict(participant_id) do nothing;
    update qs_governance.retention set state='withdrawn' where participant_id=p_id;
    update qs_governance.enrollments set withdrawn_at=coalesce(withdrawn_at,now())
        where participant_id=p_id;
    update qs_governance.extract_members set deletion_requested_at=coalesce(deletion_requested_at,now()),
        delete_by=least(coalesce(delete_by,now()+interval '72 hours'),now()+interval '72 hours',
            (select mandatory_delete_at from qs_governance.retention where participant_id=p_id))
        where participant_id=p_id and confirmed_at is null;
    -- A registered manifest which was never released has no external copy.
    update qs_governance.extract_members m set confirmed_at=now(),confirmation_reference='cancelled before release'
        from qs_governance.extracts e where m.extract_id=e.id and m.participant_id=p_id
        and e.released_at is null and m.confirmed_at is null;
    update qs_governance.extracts set state='deletion_pending' where id in (
        select extract_id from qs_governance.extract_members where participant_id=p_id and confirmed_at is null);
    update qs_governance.extracts e set state='deleted' where e.released_at is null and not exists(
        select 1 from qs_governance.extract_members m where m.extract_id=e.id and m.confirmed_at is null);
end;
$$;
create function qs_governance.finish_deletion(p_id uuid) returns void
language plpgsql security invoker set search_path='' as $$
begin
    if exists(select 1 from qs_governance.deletion_cases where participant_id=p_id and active_deleted_at is not null)
        and not exists(select 1 from qs_governance.extract_members where participant_id=p_id and confirmed_at is null) then
        update qs_governance.deletion_cases set completed_at=coalesce(completed_at,now()) where participant_id=p_id;
        update qs_governance.enrollments set purge_after=coalesce(purge_after,now()+interval '3 years')
            where participant_id=p_id;
    end if;
end;
$$;
create function public.qs_delete(p_id uuid,p_hash text) returns boolean
language plpgsql security invoker set search_path='' as $$
declare owner qs_private.participants;
begin
    select * into owner from qs_private.participants where id=p_id for update;
    if not found or owner.deleted_at is not null then return true; end if;
    if owner.capability_hash is distinct from p_hash then return false; end if;
    perform qs_governance.request_deletion(p_id,'withdrawal');
    perform qs_private.delete_storage(p_id,p_hash);
    update qs_governance.deletion_cases set active_deleted_at=now() where participant_id=p_id;
    perform qs_governance.finish_deletion(p_id);
    return true;
end;
$$;
create function public.qs_deletion_status(p_id uuid,p_receipt_hash text) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare deletion qs_governance.deletion_cases; pending integer;
begin
    select * into deletion from qs_governance.deletion_cases where participant_id=p_id;
    if not found or deletion.receipt_hash is distinct from p_receipt_hash or p_receipt_hash is null
        then raise exception 'unauthorized' using errcode='28000'; end if;
    select count(*) into pending from qs_governance.extract_members
        where participant_id=p_id and confirmed_at is null;
    return jsonb_build_object('active_storage_deleted',deletion.active_deleted_at is not null,
        'complete',deletion.completed_at is not null and pending=0,'pending_copies',pending,
        'requested_at',deletion.requested_at,'completed_at',deletion.completed_at);
end;
$$;
create function public.qs_delete_request(p_id uuid,p_hash text,p_receipt_hash text) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare owner qs_private.participants; previous qs_governance.deletion_cases;
begin
    if p_receipt_hash is null or p_receipt_hash !~ '^[0-9a-f]{64}$' then raise exception 'invalid receipt'; end if;
    select * into owner from qs_private.participants where id=p_id for update;
    select * into previous from qs_governance.deletion_cases where participant_id=p_id;
    if owner.id is null then
        -- A connection may have been saved locally before registration completed.
        -- Register a tombstone so the same stale identity can never appear later.
        -- Unknown identities consume the tighter registration allowance too,
        -- preventing the larger deletion budget from filling the identity store.
        if (public.qs_admit('register',p_receipt_hash)->>'allowed')::boolean is not true
            then raise exception 'quota_exceeded'; end if;
        if not public.qs_register(p_id,p_hash) then raise exception 'unauthorized' using errcode='28000'; end if;
    elsif owner.deleted_at is not null then
        return public.qs_deletion_status(p_id,p_receipt_hash);
    elsif owner.capability_hash is distinct from p_hash then
        raise exception 'unauthorized' using errcode='28000';
    end if;
    if not public.qs_delete(p_id,p_hash) then raise exception 'unauthorized' using errcode='28000'; end if;
    update qs_governance.deletion_cases set receipt_hash=p_receipt_hash where participant_id=p_id;
    return public.qs_deletion_status(p_id,p_receipt_hash);
end;
$$;

-- Operator-only functions. They have no Edge/anonymous execute grant and perform
-- no uncontrolled downloads; all references are inventory IDs, never raw secrets.
create function qs_governance.review_retention(p_id uuid,p_keep boolean,p_purpose text,
    p_reference text,p_next_review timestamptz,p_mandatory_deadline timestamptz default null) returns void
language plpgsql security invoker set search_path='' as $$
declare owner qs_private.participants;
begin
    select * into owner from qs_private.participants where id=p_id for update;
    if not found or owner.deleted_at is not null then raise exception 'participant unavailable'; end if;
    if p_keep is null then raise exception 'explicit review decision required'; end if;
    if length(trim(p_reference)) < 1 or p_reference is null then raise exception 'review reference required'; end if;
    if p_keep then
        if p_purpose is null or length(trim(p_purpose)) < 1 or p_next_review is null
            or p_next_review <= now() or p_next_review > now()+interval '1 year' or p_mandatory_deadline<=now()
            then raise exception 'continuing need and annual review required'; end if;
        update qs_governance.retention set purpose=p_purpose,last_review_at=now(),review_due_at=p_next_review,
            mandatory_delete_at=least(mandatory_delete_at,p_mandatory_deadline),state='active',decision_reference=p_reference
            where participant_id=p_id and state<>'withdrawn';
        if not found then raise exception 'retention record unavailable'; end if;
    else
        perform qs_governance.request_deletion(p_id,'no_continuing_need');
        perform qs_private.delete_storage(p_id,owner.capability_hash);
        update qs_governance.deletion_cases set active_deleted_at=now() where participant_id=p_id;
        perform qs_governance.finish_deletion(p_id);
    end if;
    insert into qs_governance.audit(action,object_id,reference) values('retention_review',p_id,p_reference);
end;
$$;
create function qs_governance.register_extract(p_extract uuid,p_label text,p_holder text,p_recipient uuid,
    p_purpose text,p_location text,p_access_until timestamptz,p_participants uuid[]) returns void
language plpgsql security invoker set search_path='' as $$
declare person uuid; recipient qs_governance.recipients;
begin
    if coalesce(cardinality(p_participants),0)=0 or cardinality(p_participants)>10000
        then raise exception 'explicit participant inventory required'; end if;
    if p_recipient is not null then
        select * into recipient from qs_governance.recipients where id=p_recipient for share;
        if not found or not recipient.approved or recipient.access_until < p_access_until
            then raise exception 'approved recipient required'; end if;
    end if;
    -- Deterministic participant lock order serializes release/withdrawal.
    for person in select distinct x from unnest(p_participants) x order by x loop
        perform 1 from qs_private.participants where id=person and deleted_at is null for update;
        if not found then raise exception 'participant unavailable'; end if;
        if not exists(select 1 from qs_governance.retention where participant_id=person and state='active'
            and review_due_at>now() and (mandatory_delete_at is null or mandatory_delete_at>now()))
            then raise exception 'retention review required'; end if;
    end loop;
    insert into qs_governance.extracts(id,label,holder,recipient_id,purpose,location_reference,access_until,review_due_at)
        values(p_extract,p_label,p_holder,p_recipient,p_purpose,p_location,p_access_until,least(p_access_until,now()+interval '1 year'));
    insert into qs_governance.extract_members(extract_id,participant_id)
        select p_extract,x from unnest(p_participants) x group by x;
    insert into qs_governance.audit(action,object_id,reference) values('extract_registered',p_extract,p_location);
end;
$$;
create function qs_governance.release_extract(p_extract uuid) returns setof jsonb
language plpgsql security invoker set search_path='' as $$
declare person uuid; extract qs_governance.extracts;
begin
    -- Members are frozen at registration; no additions after export are supported.
    for person in select participant_id from qs_governance.extract_members where extract_id=p_extract order by participant_id loop
        perform 1 from qs_private.participants where id=person and deleted_at is null for update;
        if not found then raise exception 'participant unavailable'; end if;
    end loop;
    select * into extract from qs_governance.extracts where id=p_extract for update;
    if not found or extract.state <> 'registered' or extract.access_until <= now() or extract.review_due_at <= now()
        then raise exception 'extract unavailable; register a new copy for each release'; end if;
    if extract.recipient_id is not null and not exists(select 1 from qs_governance.recipients
        where id=extract.recipient_id and approved and access_until >= extract.access_until)
        then raise exception 'recipient access ended'; end if;
    if exists(select 1 from qs_governance.extract_members m join qs_governance.retention r using(participant_id)
        where m.extract_id=p_extract and (m.deletion_requested_at is not null or r.state<>'active'
            or r.review_due_at<=now() or r.mandatory_delete_at<=now()))
        then raise exception 'retention review or deletion pending'; end if;
    if exists(select 1 from qs_governance.extract_members m where m.extract_id=p_extract and not exists(
        select 1 from qs_governance.enrollments e join qs_governance.protocols p on p.version=e.protocol_version
        where e.participant_id=m.participant_id and e.withdrawn_at is null and p.approved))
        then raise exception 'enrollment unavailable'; end if;
    if exists(select 1 from qs_private.submissions s join qs_governance.extract_members m using(participant_id)
        join qs_governance.enrollments e on e.id=(s.payload->>'enrollment_id')::uuid and e.withdrawn_at is null
        join qs_governance.protocols p on p.version=e.protocol_version and p.approved
        where m.extract_id=p_extract and not qs_governance.provider_fields_allowed(s.payload,p.oura_permitted,p.google_permitted))
        then raise exception 'provider permission unavailable for this extract'; end if;
    update qs_governance.extracts set state='released',released_at=now() where id=p_extract;
    insert into qs_governance.audit(action,object_id,reference) values('extract_released',p_extract,extract.location_reference);
    return query select jsonb_build_object('submission_id',s.id,'received_at',s.created_at,'package',s.payload,
        'consent_timing',jsonb_build_object('study_enrollment_id',e.id,'study_protocol_version',e.protocol_version,
            'study_consent_signed_at_client',e.evidence->'signed_at_client',
            'study_consent_received_at',e.received_at,
            'study_consent_before_first_results',e.evidence->'before_first_results'))
        from qs_private.submissions s join qs_governance.extract_members m using(participant_id)
        join qs_governance.enrollments e on e.id=(s.payload->>'enrollment_id')::uuid and e.withdrawn_at is null
        join qs_governance.protocols p on p.version=e.protocol_version and p.approved
        where m.extract_id=p_extract order by s.created_at,s.id;
end;
$$;
create function qs_governance.confirm_copy_deletion(p_extract uuid,p_participant uuid,p_reference text) returns void
language plpgsql security invoker set search_path='' as $$
begin
    if p_reference is null or length(trim(p_reference))=0 then raise exception 'deletion evidence required'; end if;
    perform 1 from qs_private.participants where id=p_participant for update;
    update qs_governance.extract_members set confirmed_at=coalesce(confirmed_at,now()),confirmation_reference=p_reference
        where extract_id=p_extract and participant_id=p_participant and deletion_requested_at is not null;
    if not found then raise exception 'no deletion task'; end if;
    if not exists(select 1 from qs_governance.extract_members where extract_id=p_extract and confirmed_at is null) then
        update qs_governance.extracts set state='deleted' where id=p_extract;
    elsif not exists(select 1 from qs_governance.extract_members where extract_id=p_extract
        and deletion_requested_at is not null and confirmed_at is null) then
        update qs_governance.extracts set state=case when review_due_at<=now() then 'held' else 'released' end
            where id=p_extract;
    end if;
    perform qs_governance.finish_deletion(p_participant);
    insert into qs_governance.audit(action,object_id,reference) values('copy_deletion_confirmed',p_extract,p_reference);
end;
$$;
create function qs_governance.review_extract(p_extract uuid,p_purpose text,p_reference text,p_access_until timestamptz)
returns void language plpgsql security invoker set search_path='' as $$
declare person uuid; extract qs_governance.extracts;
begin
    for person in select participant_id from qs_governance.extract_members where extract_id=p_extract
        and confirmed_at is null order by participant_id loop
        perform 1 from qs_private.participants where id=person and deleted_at is null for update;
        if not found or not exists(select 1 from qs_governance.retention where participant_id=person
            and state='active' and review_due_at>now() and (mandatory_delete_at is null or mandatory_delete_at>now()))
            then raise exception 'participant review or deletion pending'; end if;
    end loop;
    select * into extract from qs_governance.extracts where id=p_extract for update;
    if not found or extract.state not in ('registered','released','held') or p_access_until is null
        or p_access_until<=now() or p_purpose is null or length(trim(p_purpose))=0
        or p_reference is null or length(trim(p_reference))=0
        then raise exception 'current copy and documented need required'; end if;
    if extract.recipient_id is not null and not exists(select 1 from qs_governance.recipients
        where id=extract.recipient_id and approved and access_until>=p_access_until)
        then raise exception 'recipient access unavailable'; end if;
    update qs_governance.extracts set purpose=p_purpose,access_until=p_access_until,
        review_due_at=least(p_access_until,now()+interval '1 year'),
        state=case when released_at is null then 'registered' else 'released' end where id=p_extract;
    insert into qs_governance.audit(action,object_id,reference) values('extract_review',p_extract,p_reference);
end;
$$;
create function qs_governance.maintain_research() returns void
language plpgsql security invoker set search_path='' as $$
declare person record;
begin
    if not pg_try_advisory_xact_lock(72843108) then return; end if;
    for person in select p.id,p.capability_hash from qs_private.participants p
        join qs_governance.retention r on r.participant_id=p.id
        where p.deleted_at is null and r.mandatory_delete_at<=now() order by p.id for update of p loop
        perform qs_governance.request_deletion(person.id,'mandatory_deadline');
        perform qs_private.delete_storage(person.id,person.capability_hash);
        update qs_governance.deletion_cases set active_deleted_at=now() where participant_id=person.id;
        perform qs_governance.finish_deletion(person.id);
    end loop;
    update qs_governance.retention set state='held' where state='active' and review_due_at<=now();
    update qs_governance.extracts set state='held' where state in ('registered','released') and review_due_at<=now();
    -- Expired/revoked access creates tasks for every remaining copy. No remote
    -- filesystem is presumed erased until an operator records confirmation.
    update qs_governance.extract_members m set deletion_requested_at=coalesce(m.deletion_requested_at,now()),
        delete_by=least(coalesce(m.delete_by,now()+interval '72 hours'),e.access_until)
        from qs_governance.extracts e left join qs_governance.recipients c on c.id=e.recipient_id
        where m.extract_id=e.id and m.confirmed_at is null and
            (e.access_until<=now()
                or (e.recipient_id is not null and (not c.approved or c.access_until<=now())));
    update qs_governance.extracts e set state='deletion_pending' where state<>'deleted' and exists(
        select 1 from qs_governance.extract_members m where m.extract_id=e.id
            and m.deletion_requested_at is not null and m.confirmed_at is null);
    delete from qs_governance.enrollments e where e.purge_after<=now()
        and not exists(select 1 from qs_private.submissions s where s.payload->>'enrollment_id'=e.id::text)
        and not exists(
        select 1 from qs_governance.extract_members m where m.participant_id=e.participant_id and m.confirmed_at is null);
    delete from qs_governance.extract_members m using qs_governance.deletion_cases d
        where m.participant_id=d.participant_id and d.completed_at<now()-interval '3 years';
    delete from qs_governance.retention r using qs_governance.deletion_cases d
        where r.participant_id=d.participant_id and d.completed_at<now()-interval '3 years';
    delete from qs_governance.deletion_cases where completed_at<now()-interval '3 years';
    delete from qs_governance.audit where occurred_at<now()-interval '3 years';
    -- No measurement age-based deletion: an overdue review holds use, rather
    -- than interpreting silence or another upload as a decision to retain.
    perform qs_private.set_alert('research_review_overdue',exists(select 1 from qs_governance.retention where state='held')
        or exists(select 1 from qs_governance.extracts where state='held'));
    perform qs_private.set_alert('research_deletion_overdue',exists(select 1 from qs_governance.extract_members
        where deletion_requested_at is not null and confirmed_at is null and delete_by<=now()));
end;
$$;
create function qs_governance.replay_deletions() returns integer
language plpgsql security invoker set search_path='' as $$
declare person record; removed integer := 0; count_removed integer;
begin
    -- Run in an isolated restored database AFTER importing the current deletion
    -- ledger. A backup's stale copy of its own ledger is not authoritative.
    for person in select p.id from qs_private.participants p
        where p.deleted_at is not null or exists(select 1 from qs_governance.deletion_cases d where d.participant_id=p.id)
        order by p.id for update of p loop
        perform qs_governance.request_deletion(person.id,'withdrawal');
        delete from qs_private.submissions where participant_id=person.id;
        get diagnostics count_removed = row_count;
        removed := removed + count_removed;
        update qs_private.participants set capability_hash=null,deleted_at=coalesce(deleted_at,now()) where id=person.id;
        update qs_governance.deletion_cases set active_deleted_at=coalesce(active_deleted_at,now()) where participant_id=person.id;
        perform qs_governance.finish_deletion(person.id);
    end loop;
    perform 1 from qs_private.storage_budget where singleton for update;
    update qs_private.storage_budget set submissions=(select count(*) from qs_private.submissions),
        payload_bytes=(select coalesce(sum(payload_bytes),0) from qs_private.submissions) where singleton;
    return removed;
end;
$$;
alter function qs_private.maintain_operations() rename to maintain_operations_base;
create function qs_private.maintain_operations() returns void
language plpgsql security invoker set search_path='' as $$
begin
    perform qs_private.maintain_operations_base();
    perform qs_governance.maintain_research();
end;
$$;

revoke all on all functions in schema qs_governance from public,anon,authenticated,service_role;
revoke all on all sequences in schema qs_governance from public,anon,authenticated,service_role;
grant execute on function qs_governance.request_deletion(uuid,text),qs_governance.finish_deletion(uuid) to service_role;
grant execute on function qs_governance.provider_fields_allowed(jsonb,boolean,boolean) to service_role;
revoke all on function public.qs_enrollment_information(),public.qs_enroll(uuid,text,jsonb),
    public.qs_submit(uuid,text,uuid,jsonb,text),public.qs_delete(uuid,text),
    public.qs_delete_request(uuid,text,text),public.qs_deletion_status(uuid,text) from public,anon,authenticated;
grant execute on function public.qs_enrollment_information(),public.qs_enroll(uuid,text,jsonb),
    public.qs_submit(uuid,text,uuid,jsonb,text),public.qs_delete(uuid,text),
    public.qs_delete_request(uuid,text,text),public.qs_deletion_status(uuid,text) to service_role;
revoke all on function qs_private.maintain_operations() from public,anon,authenticated,service_role;

-- Independent scheduler registration also works on an already-running backend;
-- it does not depend on an older monitor's cached function-call plan.
do $schedule$
begin
    if exists(select 1 from pg_extension where extname='pg_cron') then
        perform cron.schedule('qs-research-maintenance','*/5 * * * *',
            $job$select qs_governance.maintain_research();
            delete from cron.job_run_details where jobid in
                (select jobid from cron.job where jobname='qs-research-maintenance')
                and end_time < now()-interval '7 days';$job$);
    end if;
end;
$schedule$;
