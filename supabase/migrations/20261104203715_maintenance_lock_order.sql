-- Lock participants before any shared accounting or copy-state mutations.
-- A cursor can fetch later participants after deleting its first batch; without
-- the initial lock pass, a withdrawal can own that participant while waiting
-- for the budget row already held by maintenance. This replaces no policy,
-- approval, provider permission or feature flag.
create or replace function qs_governance.maintain_research() returns void
language plpgsql security invoker set search_path='' as $$
declare person record;
begin
    if not pg_try_advisory_xact_lock(72843108) then return; end if;
    -- PERFORM consumes the complete result before the deletion loop starts.
    -- Include tombstones because copy confirmations also lock their participant.
    perform 1 from qs_private.participants order by id for update;
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
