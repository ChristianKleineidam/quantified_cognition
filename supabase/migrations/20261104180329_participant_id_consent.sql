-- Preserve historical signed bundles/evidence. New enrollments use an explicit
-- consent action linked to the existing public profile participant ID.
alter table qs_governance.protocols drop constraint protocols_signature_method_check;
alter table qs_governance.protocols add constraint protocols_signature_method_check
    check (signature_method in ('typed_pseudonym','explicit_consent'));

create or replace function public.qs_enroll(p_id uuid,p_hash text,p_evidence jsonb) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare owner qs_private.participants; protocol qs_governance.protocols;
    previous qs_governance.enrollments; consent_id uuid; public_id uuid;
    fields constant text[] := array['enrollment_id','participant_id','version','bundle_hash',
        'adult','own_data','explicit_consent','signature_method','signed_at_client',
        'before_first_results','choice_event_id','notice_version','notice_hash'];
begin
    select * into owner from qs_private.participants where id=p_id for update;
    if not found or owner.deleted_at is not null or owner.capability_hash is distinct from p_hash
        then raise exception 'unauthorized' using errcode='28000'; end if;
    if jsonb_typeof(p_evidence) is distinct from 'object' then
        raise exception 'invalid consent' using errcode='22023'; end if;
    if not p_evidence ?& fields or p_evidence - fields <> '{}'::jsonb
        then raise exception 'invalid consent' using errcode='22023'; end if;
    select * into protocol from qs_governance.protocols
        where version=p_evidence->>'version' and approved and enrollment_open for share;
    if not found or protocol.bundle_hash is distinct from p_evidence->>'bundle_hash'
        or protocol.notice_version is distinct from p_evidence->>'notice_version'
        or protocol.notice_hash is distinct from p_evidence->>'notice_hash'
        or protocol.signature_method <> 'explicit_consent'
        or protocol.signature_method is distinct from p_evidence->>'signature_method'
        then raise exception 'enrollment_required' using errcode='42501'; end if;
    if p_evidence->'adult' is distinct from 'true'::jsonb
        or p_evidence->'own_data' is distinct from 'true'::jsonb
        or p_evidence->'explicit_consent' is distinct from 'true'::jsonb
        or jsonb_typeof(p_evidence->'enrollment_id') is distinct from 'string'
        or jsonb_typeof(p_evidence->'participant_id') is distinct from 'string'
        or jsonb_typeof(p_evidence->'signed_at_client') is distinct from 'string'
        or not isfinite((p_evidence->>'signed_at_client')::timestamptz)
        or (p_evidence->>'signed_at_client')::timestamptz > now()+interval '1 day'
        or p_evidence->'before_first_results' not in ('true'::jsonb,'false'::jsonb,'null'::jsonb)
        or jsonb_typeof(p_evidence->'choice_event_id') is distinct from 'number'
        or p_evidence->>'choice_event_id' !~ '^[1-9][0-9]*$'
        then raise exception 'invalid consent' using errcode='22023'; end if;
    consent_id := (p_evidence->>'enrollment_id')::uuid;
    public_id := (p_evidence->>'participant_id')::uuid;
    -- A renewal must retain the public participant ID already bound to this
    -- authenticated connection. Measurements are checked against the same ID.
    if exists(select 1 from qs_governance.enrollments
        where participant_id=p_id and public_participant_id <> public_id)
        then raise exception 'enrollment conflict' using errcode='23505'; end if;
    select * into previous from qs_governance.enrollments where id=consent_id;
    if found then
        if previous.participant_id <> p_id or previous.evidence <> p_evidence
            then raise exception 'enrollment conflict' using errcode='23505'; end if;
        if previous.withdrawn_at is not null then raise exception 'enrollment_required' using errcode='42501'; end if;
        return jsonb_build_object('enrollment_id',previous.id,'received_at',previous.received_at);
    end if;
    -- Renewing replaces the active enrollment without resetting retention review.
    update qs_governance.enrollments set superseded_at=now(),purge_after=now()+interval '3 years'
        where participant_id=p_id and withdrawn_at is null and superseded_at is null;
    insert into qs_governance.enrollments(id,participant_id,public_participant_id,protocol_version,evidence)
        values(consent_id,p_id,public_id,protocol.version,p_evidence) returning * into previous;
    insert into qs_governance.retention(participant_id,purpose,decision_reference)
        values(p_id,'Learning and recall variation; sleep, activity, study patterns and measurement validation',
            'initial enrollment: ' || protocol.version) on conflict(participant_id) do nothing;
    return jsonb_build_object('enrollment_id',previous.id,'received_at',previous.received_at);
end;
$$;
-- CREATE OR REPLACE preserves the existing invoker mode and RPC permissions.
-- No protocol approval, enrollment opening or provider permission is granted.
