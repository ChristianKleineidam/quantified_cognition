-- Ciphertext is opaque to the server. Consent/retention/provider declarations,
-- byte quotas, idempotency and deletion remain server-enforced. Scientific rows
-- are allowlist-validated locally before encryption and after decryption.
create function qs_governance.valid_encrypted_package(p jsonb) returns boolean
language plpgsql immutable security invoker set search_path='' as $$
declare item jsonb; keys text[];
begin
    if jsonb_typeof(p) is distinct from 'object' then return false; end if;
    select array_agg(k order by k) into keys from jsonb_object_keys(p) k;
    if keys is distinct from array['algorithm','ciphertext','compression','created_at','enc','enrollment_id',
        'envelope_version','key_id','metadata','providers','row_count','submission_id']::text[]
        then return false; end if;
    if exists(select 1 from unnest(array['algorithm','ciphertext','created_at','enc',
        'enrollment_id','key_id','submission_id']) k where jsonb_typeof(p->k) is distinct from 'string')
        then return false; end if;
    if p->'envelope_version' is distinct from '1'::jsonb
        or p->>'algorithm' is distinct from 'HPKE-X25519-HKDF-SHA256-AES256GCM'
        or p->>'compression' is distinct from 'zlib'
        or coalesce(p->>'key_id','') !~ '^[0-9a-f]{64}$'
        or coalesce(p->>'enrollment_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        or coalesce(p->>'submission_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        or jsonb_typeof(p->'row_count') is distinct from 'number'
        or coalesce(p->>'row_count','') !~ '^[1-9][0-9]{0,4}$'
        or (p->>'row_count')::integer > 36600
        or jsonb_typeof(p->'created_at') is distinct from 'string'
        or length(p->>'created_at') > 64
        then return false; end if;
    if jsonb_typeof(p->'metadata') is distinct from 'object'
        or jsonb_typeof(p->'providers') is distinct from 'array'
        then return false; end if;
    select array_agg(k order by k) into keys from jsonb_object_keys(p->'metadata') k;
    if exists(select 1 from jsonb_each(p->'metadata') e where jsonb_typeof(e.value) is distinct from 'string')
        then return false; end if;
    if keys is distinct from array['research_participant_id','sharing_notice_hash','sharing_notice_version']::text[]
        or coalesce(p->'metadata'->>'research_participant_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        or coalesce(p->'metadata'->>'sharing_notice_hash','') !~ '^[0-9a-f]{64}$'
        or coalesce(length(p->'metadata'->>'sharing_notice_version'),0) not between 1 and 256
        then return false; end if;
    if jsonb_array_length(p->'providers') > 2 or
        (select count(distinct v) from jsonb_array_elements(p->'providers') v) <> jsonb_array_length(p->'providers')
        then return false; end if;
    for item in select v from jsonb_array_elements(p->'providers') v loop
        if item not in ('"oura"'::jsonb,'"google_health"'::jsonb) then return false; end if;
    end loop;
    if coalesce(p->>'enc','') !~ '^[A-Za-z0-9+/]{43}=$'
        or coalesce(p->>'ciphertext','') !~ '^[A-Za-z0-9+/]+={0,2}$'
        or length(p->>'ciphertext') > 8388640
        or octet_length(decode(p->>'enc','base64')) <> 32
        or octet_length(decode(p->>'ciphertext','base64')) not between 16 and 6291472
        then return false; end if;
    return true;
exception when invalid_text_representation or numeric_value_out_of_range or invalid_parameter_value then
    return false;
end;
$$;
revoke all on function qs_governance.valid_encrypted_package(jsonb) from public,anon,authenticated;
grant execute on function qs_governance.valid_encrypted_package(jsonb) to service_role;

create or replace function qs_governance.provider_fields_allowed(p_payload jsonb,p_oura boolean,p_google boolean)
returns boolean language plpgsql immutable security invoker set search_path='' as $$
begin
    if p_payload ? 'envelope_version' then
        return qs_governance.valid_encrypted_package(p_payload)
            and (p_oura or not p_payload->'providers' ? 'oura')
            and (p_google or not p_payload->'providers' ? 'google_health');
    end if;
    return not exists(select 1 from jsonb_array_elements_text(p_payload->'columns') with ordinality c(name,pos)
        cross join lateral jsonb_array_elements(p_payload->'rows') r(value)
        where ((c.name like 'oura_%' and not p_oura) or (c.name like 'google_health_%' and not p_google))
            and coalesce(r.value->>(c.pos::integer-1),'') <> '');
end;
$$;
revoke all on function qs_governance.provider_fields_allowed(jsonb,boolean,boolean) from public,anon,authenticated;
grant execute on function qs_governance.provider_fields_allowed(jsonb,boolean,boolean) to service_role;

create or replace function public.qs_submit(p_id uuid,p_hash text,p_submission uuid,p_payload jsonb,p_payload_hash text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare owner qs_private.participants; enrollment qs_governance.enrollments;
    protocol qs_governance.protocols; retention qs_governance.retention;
begin
    select * into owner from qs_private.participants where id=p_id for update;
    if not found or owner.deleted_at is not null or owner.capability_hash is distinct from p_hash
        then raise exception 'unauthorized' using errcode='28000'; end if;
    -- API writers can store only authenticated-encryption envelopes. Historical
    -- plaintext fixtures/records remain readable by the database operator.
    if current_user='service_role' and not coalesce(p_payload ? 'envelope_version',false)
        then raise exception 'encrypted_package_required' using errcode='42501'; end if;
    if p_payload ? 'envelope_version' and (not qs_governance.valid_encrypted_package(p_payload)
        or p_payload->>'key_id' <> '07aabc3f826551f9a7bd7c7b9ed84ed04e0535afef7a8231e9df8fbb4feb53a3'
        or p_payload->>'submission_id' <> p_submission::text)
        then raise exception 'invalid_encrypted_package' using errcode='22023'; end if;
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
