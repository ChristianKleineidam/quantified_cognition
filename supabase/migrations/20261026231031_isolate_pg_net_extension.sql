-- pg_net is not relocatable. Recreate only the extension just installed for
-- monitoring, without CASCADE; an unexpected dependency aborts the migration.
do $$
begin
    if exists(select 1 from pg_extension e join pg_namespace n on n.oid=e.extnamespace
        where e.extname='pg_net' and n.nspname='public') then
        drop extension pg_net;
        create extension pg_net with schema extensions;
        update qs_private.ops_state set probe_id=null,probe_checked_at=null,probe_ok=null where singleton;
    end if;
end;
$$;
