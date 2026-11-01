-- Supabase has both extensions. Portable PostgreSQL tests deliberately skip only
-- the scheduler adapter; maintain_operations and all quotas are tested there.
do $setup$
begin
    if exists(select 1 from pg_available_extensions where name='pg_cron')
        and exists(select 1 from pg_available_extensions where name='pg_net') then
        create extension if not exists pg_cron;
        create extension if not exists pg_net;
        execute $function$
        create function qs_private.monitor_tick() returns void
        language plpgsql security invoker set search_path = '' as $body$
        declare state qs_private.ops_state; response record; data jsonb; healthy boolean := false;
        begin
            -- Do not overlap a manual probe and the scheduled invocation.
            if not pg_try_advisory_xact_lock(72843107) then return; end if;
            perform qs_private.maintain_operations();
            select * into state from qs_private.ops_state where singleton for update;
            if state.probe_id is not null then
                select status_code,content,error_msg,timed_out,created into response
                    from net._http_response where id=state.probe_id;
                if found and response.status_code=200 and response.error_msg is null
                    and not coalesce(response.timed_out,false) then
                    begin
                        data := response.content::jsonb;
                        healthy := data->'oura_enabled'='false'::jsonb
                            and data->'google_health_enabled'='false'::jsonb
                            and data->'research_enabled'='false'::jsonb
                            and data->'operations_version'='1'::jsonb;
                    exception when others then healthy := false;
                    end;
                end if;
                healthy := coalesce(healthy,false);
                update qs_private.ops_state set probe_checked_at=now(),probe_ok=healthy where singleton;
                perform qs_private.set_alert('closed_backend_check_failed',not healthy);
                perform qs_private.set_alert('feature_unexpectedly_enabled',coalesce(
                    data->'oura_enabled'='true'::jsonb or data->'google_health_enabled'='true'::jsonb
                    or data->'research_enabled'='true'::jsonb,false));
            end if;
            -- Public status only: no credentials or participant data in cron or pg_net.
            update qs_private.ops_state set probe_id=net.http_get(
                url:='https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service/status',
                timeout_milliseconds:=10000) where singleton;
            delete from cron.job_run_details where jobid in
                (select jobid from cron.job where jobname='qs-operations-monitor')
                and end_time < now()-interval '7 days';
        end;
        $body$
        $function$;
        revoke all on function qs_private.monitor_tick() from public,anon,authenticated,service_role;
        -- The scheduler runs as the migration's administrative role, never an API user.
        perform cron.schedule('qs-operations-monitor','*/5 * * * *','select qs_private.monitor_tick()');
    end if;
end;
$setup$;
