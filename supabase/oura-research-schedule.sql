-- Operator-only commissioning. Create the Vault secret qs_oura_research_job
-- outside source control, matching the Edge OURA_RESEARCH_JOB_SECRET.
-- The credential encryption key exists only in the Edge secret store.
select cron.schedule('qs-oura-research-authorization','* * * * *',$job$
    select net.http_post(
        url:='https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service/internal/oura-authorization',
        headers:=jsonb_build_object('Content-Type','application/json','Authorization','Bearer '||(
            select decrypted_secret from vault.decrypted_secrets where name='qs_oura_research_job')),
        body:='{}'::jsonb,timeout_milliseconds:=70000);
$job$);
