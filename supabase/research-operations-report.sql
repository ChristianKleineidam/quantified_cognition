-- Authorized operator only. No signatures, credentials or measurement values.
select version,approved,enrollment_open,signature_method,oura_permitted,google_permitted
from qs_governance.protocols order by version;

select state,count(*) as identities,min(review_due_at) as earliest_review,
    min(mandatory_delete_at) as earliest_mandatory_deletion
from qs_governance.retention group by state;

select r.participant_id,r.state,r.purpose,r.last_review_at,r.review_due_at,
       r.mandatory_delete_at,r.decision_reference
from qs_governance.retention r where r.state<>'withdrawn'
and (r.review_due_at<=now()+interval '30 days' or r.mandatory_delete_at<=now()+interval '30 days')
order by least(r.review_due_at,r.mandatory_delete_at),r.participant_id;

select e.id as extract_id,e.label,e.holder,e.location_reference,e.state,
       c.organization,c.responsible_person,c.country,c.approved as recipient_approved,
       e.access_until,e.review_due_at,m.participant_id,m.deletion_requested_at,m.delete_by,
       m.delete_by<=now() as overdue
from qs_governance.extract_members m join qs_governance.extracts e on e.id=m.extract_id
left join qs_governance.recipients c on c.id=e.recipient_id
where m.deletion_requested_at is not null and m.confirmed_at is null
order by m.delete_by,e.id,m.participant_id;

select participant_id,requested_at,active_deleted_at,completed_at,reason
from qs_governance.deletion_cases where completed_at is null order by requested_at;

select id,label,holder,location_reference,review_due_at,access_until
from qs_governance.extracts where state='held' or
    (state in ('registered','released') and review_due_at<=now()+interval '30 days')
order by review_due_at,id;

select code,active,first_seen,last_seen from qs_private.ops_alerts
where code in ('research_review_overdue','research_deletion_overdue') order by code;
