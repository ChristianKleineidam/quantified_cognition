-- Administrative, rollback-only verification. No real measurements or identities.
-- All temporary policy changes, synthetic rows, counters and probe edits roll back.
begin;
do $$
declare actor text := repeat('d',64); participant uuid := gen_random_uuid();
    submission uuid := gen_random_uuid(); receipt jsonb; result jsonb; state qs_private.ops_state;
begin
    delete from qs_private.quota_buckets where family='submit';
    update qs_private.quota_policy set global_minute=2,global_day=2,actor_minute=2,actor_day=2 where family='submit';
    assert (public.qs_admit('submit',actor)->>'allowed')::boolean;
    assert (public.qs_admit('submit',actor)->>'allowed')::boolean;
    result := public.qs_admit('submit',repeat('e',64));
    assert result->'allowed'='false'::jsonb and (result->>'retry_after')::integer>0;
    assert (select used=2 from qs_private.quota_buckets where family='submit' and subject='global' and window_seconds=60);
    assert (public.qs_admit('delete',actor)->>'allowed')::boolean;

    assert public.qs_register(participant,actor);
    update qs_private.storage_budget set participant_submission_limit=1;
    -- Operator-only storage probe; public submission additionally requires enrollment.
    receipt := qs_private.store_submission(participant,actor,submission,'{"synthetic":true}'::jsonb,actor);
    assert qs_private.store_submission(participant,actor,submission,'{"synthetic":true}'::jsonb,actor)=receipt;
    begin
        perform qs_private.store_submission(participant,actor,gen_random_uuid(),'{}'::jsonb,actor);
        raise exception 'expected storage quota';
    exception when raise_exception then assert sqlerrm='quota_exceeded';
    end;
    assert public.qs_delete(participant,actor);
    assert not public.qs_register(participant,actor);
    assert not exists(select 1 from qs_private.submissions where participant_id=participant);

    perform public.qs_ops_record('submit',502,21000) from generate_series(1,10);
    perform qs_private.maintain_operations();
    assert (select active from qs_private.ops_alerts where code='service_errors');
    assert (select active from qs_private.ops_alerts where code='slow_requests');

    -- Exercise actual hosted pg_net parsing without enabling any feature or
    -- making a real OAuth/upload request. These transient response edits roll back.
    select * into state from qs_private.ops_state where singleton;
    assert state.probe_id is not null;
    assert exists(select 1 from net._http_response where id=state.probe_id and status_code=200);
    update net._http_response set content='{"oura_enabled":true,"google_health_enabled":false,"research_enabled":false,"operations_version":1}'
        where id=state.probe_id;
    perform qs_private.monitor_tick();
    assert (select active from qs_private.ops_alerts where code='feature_unexpectedly_enabled');
    assert (select active from qs_private.ops_alerts where code='closed_backend_check_failed');
    update qs_private.ops_state set probe_id=state.probe_id where singleton;
    update net._http_response set status_code=503,content='{}' where id=state.probe_id;
    perform qs_private.monitor_tick();
    assert (select active from qs_private.ops_alerts where code='closed_backend_check_failed');
    update qs_private.ops_state set probe_id=state.probe_id where singleton;
    update net._http_response set status_code=200,
        content='{"oura_enabled":false,"google_health_enabled":false,"research_enabled":false,"operations_version":1}' where id=state.probe_id;
    perform qs_private.monitor_tick();
    assert not (select active from qs_private.ops_alerts where code='closed_backend_check_failed');
    assert not (select active from qs_private.ops_alerts where code='feature_unexpectedly_enabled');
end;
$$;
rollback;
select 'passed; all synthetic changes rolled back' as operational_checks;
