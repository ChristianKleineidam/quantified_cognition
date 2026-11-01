-- Operator-only report. One result works with both the CLI and SQL editor.
-- No payloads, credentials, actor hashes or participant identities.
select jsonb_build_object(
    'checked_at',now(),
    'health',(select to_jsonb(h) from (
        select maintenance_at,probe_checked_at,probe_ok,
            maintenance_at is null or maintenance_at < now()-interval '12 minutes' as maintenance_stale,
            probe_checked_at is null or probe_checked_at < now()-interval '12 minutes' as probe_stale
        from qs_private.ops_state) h),
    'active_alerts',coalesce((select jsonb_agg(to_jsonb(a)) from (
        select code,first_seen,last_seen from qs_private.ops_alerts where active order by code) a),'[]'::jsonb),
    'storage',(select to_jsonb(s)-'singleton' from qs_private.storage_budget s),
    'traffic_24h',coalesce((select jsonb_agg(to_jsonb(t)) from (
        select route,status,sum(requests) as requests,sum(duration_ms) as duration_ms,max(max_duration_ms) as max_duration_ms
        from qs_private.ops_metrics where minute>now()-interval '24 hours' group by route,status order by route,status) t),'[]'::jsonb),
    'global_quotas',coalesce((select jsonb_agg(to_jsonb(q)) from (
        select family,window_seconds,window_start,used from qs_private.quota_buckets
        where subject='global' and window_start+make_interval(secs=>window_seconds)>now() order by family,window_seconds) q),'[]'::jsonb)
) as operations;
