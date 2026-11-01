-- Run as an authorized researcher in the SQL editor, never as an anon client.
-- Latest snapshot per public participant/day; keep submission provenance.
with daily as (
    select s.id as submission_id, s.created_at as received_at,
           s.payload -> 'metadata' as metadata,
           jsonb_object_agg(cols.name, vals.value) as measurements
    from qs_private.submissions s
    cross join lateral jsonb_array_elements(s.payload -> 'rows') with ordinality rows(value, idx)
    cross join lateral jsonb_array_elements_text(s.payload -> 'columns') with ordinality cols(name, idx)
    join lateral jsonb_array_elements(rows.value) with ordinality vals(value, idx)
        on vals.idx = cols.idx
    group by s.id, s.created_at, s.payload -> 'metadata', rows.idx
), ranked as (
    select *, row_number() over (
        partition by metadata ->> 'research_participant_id', measurements ->> 'date'
        order by received_at desc, submission_id desc
    ) as snapshot_rank from daily
)
select submission_id, received_at, metadata || measurements as daily_record
from ranked where snapshot_rank = 1
order by metadata ->> 'research_participant_id', measurements ->> 'date';
