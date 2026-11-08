-- Enable/disable expectations are operator configuration, independent of consent.
alter table qs_private.ops_state add column research_expected_enabled boolean not null default false;
-- Edge RPCs may refresh heartbeats, but cannot change operator expectations.
revoke insert,update,delete on qs_private.ops_state from service_role;
grant update(maintenance_at,research_maintenance_at,oura_authorization_monitor_at,
    oura_authorization_monitor_required) on qs_private.ops_state to service_role;

create function qs_private.feature_state_matches(p_status jsonb) returns boolean
language sql stable security invoker set search_path='' as $$
    select coalesce((select
        p_status->'oura_enabled'=to_jsonb(oura_expected_enabled)
        and p_status->'research_enabled'=to_jsonb(research_expected_enabled)
        and p_status->'google_health_enabled'='false'::jsonb
        and p_status->'operations_version'='1'::jsonb
        from qs_private.ops_state where singleton),false);
$$;
revoke all on function qs_private.feature_state_matches(jsonb)
    from public,anon,authenticated,service_role,qs_monitor_reader,qs_backup_agent;

-- The cron/pg_net function is absent in isolated test clusters.
do $setup$ begin
    if to_regprocedure('qs_private.monitor_tick()') is not null then
        execute $function$
        create or replace function qs_private.monitor_tick() returns void
        language plpgsql security invoker set search_path='' as $body$
        declare state qs_private.ops_state; response record; data jsonb; healthy boolean:=false;
        begin
            if not pg_try_advisory_xact_lock(72843107) then return; end if;
            perform qs_private.maintain_operations();
            select * into state from qs_private.ops_state where singleton for update;
            if state.probe_id is not null then
                select status_code,content,error_msg,timed_out,created into response
                    from net._http_response where id=state.probe_id;
                if found and response.status_code=200 and response.error_msg is null
                    and not coalesce(response.timed_out,false) then
                    begin
                        data:=response.content::jsonb;
                        healthy:=qs_private.feature_state_matches(data);
                    exception when others then healthy:=false;
                    end;
                end if;
                update qs_private.ops_state set probe_checked_at=now(),probe_ok=healthy where singleton;
                perform qs_private.set_alert('closed_backend_check_failed',false);
                perform qs_private.set_alert('backend_state_check_failed',not healthy);
                perform qs_private.set_alert('feature_unexpectedly_enabled',coalesce(
                    (data->'oura_enabled'='true'::jsonb and not state.oura_expected_enabled)
                    or data->'google_health_enabled'='true'::jsonb
                    or (data->'research_enabled'='true'::jsonb and not state.research_expected_enabled),false));
            end if;
            update qs_private.ops_state set probe_id=net.http_get(
                url:='https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service/status',
                timeout_milliseconds:=10000) where singleton;
            delete from cron.job_run_details where jobid in
                (select jobid from cron.job where jobname='qs-operations-monitor')
                and end_time<now()-interval '7 days';
        end;
        $body$
        $function$;
    end if;
end; $setup$;
