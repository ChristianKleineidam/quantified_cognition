-- Count accepted first uploads without exposing participant or submission rows
-- to the monitor. Historical submissions are deliberately not counted.
alter table qs_private.storage_budget
    add column first_research_uploads bigint not null default 0 check (first_research_uploads >= 0);

create function qs_private.count_first_research_upload() returns trigger
language plpgsql security invoker set search_path = '' as $$
begin
    -- store_submission holds the participant and budget locks before inserting.
    if not exists (
        select 1 from qs_private.submissions
        where participant_id = new.participant_id and id <> new.id
    ) then
        update qs_private.storage_budget
        set first_research_uploads = first_research_uploads + 1
        where singleton;
    end if;
    return null;
end;
$$;

revoke all on function qs_private.count_first_research_upload() from public, anon, authenticated;
grant execute on function qs_private.count_first_research_upload() to service_role;

-- Administrator restores and imports must not generate new-upload alerts.
create trigger count_first_research_upload after insert on qs_private.submissions
for each row when (current_user = 'service_role')
execute function qs_private.count_first_research_upload();
