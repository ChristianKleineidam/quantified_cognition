-- Operator only. Register a NEW extract inventory first, then set its UUID:
-- select set_config('qs.extract_id','<registered extract UUID>',false);
-- Each export is one tracked release. Re-running requires a new manifest.
with released as materialized (
    select (v->>'submission_id')::uuid id,(v->>'received_at')::timestamptz created_at,
           v->'package' payload,v->'consent_timing' consent_timing
    from qs_governance.release_extract(current_setting('qs.extract_id',true)::uuid) v
), daily as (
    select s.id as submission_id, s.created_at as received_at,
           (s.payload -> 'metadata') || s.consent_timing as metadata,
           jsonb_object_agg(cols.name, vals.value) as measurements
    from released s
    cross join lateral jsonb_array_elements(s.payload -> 'rows') with ordinality rows(value, idx)
    cross join lateral jsonb_array_elements_text(s.payload -> 'columns') with ordinality cols(name, idx)
    join lateral jsonb_array_elements(rows.value) with ordinality vals(value, idx)
        on vals.idx = cols.idx
    group by s.id, s.created_at, s.payload -> 'metadata', s.consent_timing, rows.idx
), ranked as (
    select *, row_number() over (
        partition by metadata ->> 'research_participant_id', measurements ->> 'date'
        order by received_at desc, submission_id desc
    ) as snapshot_rank from daily
)
select submission_id, received_at, metadata || measurements as daily_record
from ranked where snapshot_rank = 1
order by metadata ->> 'research_participant_id', measurements ->> 'date';
