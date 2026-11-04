# Deploy health OAuth services and research storage

The dedicated **anki-quantified-self** project is deployed in Tempo's Frankfurt
region (`eu-central-1`), with **Oura access, Google Health access and research uploads explicitly
disabled**. See [DEPLOYED.md](DEPLOYED.md) for the verified endpoints and state.
The information website and policy are published; provider and research approvals
are still pending. No email/password accounts are
required from Anki users. Supabase Auth is not used as a health provider.

JollyJoyJourney is the company named in the privacy policy. Its registered
business address has been verified against the trade-registration certificate.
The contact address is [contact@jollyjoyjourney.com](mailto:contact@jollyjoyjourney.com),
forwarded through Cloudflare Email Routing to the operator's verified Gmail inbox.
The published policy URL is
`https://quantifiedcognition.jollyjoyjourney.com/privacy/`; see [WEBSITE.md](WEBSITE.md).
The local environment example uses that URL. Hosted operator/policy fields have
not been changed by website publication; the three enable flags remain false.

## Components

`supabase/functions/qs-service/index.ts` is the Deno entry point. The pure Web API
handler in `service.mjs` has no third-party runtime dependencies. OAuth start
and callback use signed, ten-minute flows; code exchange verifies PKCE. The
backend only proxies fixed Oura/Google token/revoke endpoints and never arbitrary URLs.
Google routes live in `google.mjs`; their signed flows cannot be exchanged on Oura routes.
Health data is fetched directly by the desktop. Tokens pass through the service
but are not stored in its database. Configure request logging accordingly.

Research packages are actually stored in **Supabase PostgreSQL**, as immutable
JSONB rows in `qs_private.submissions`. Supabase object Storage buckets are not
needed for these small structured packages. `qs_private.participants` stores
only a random upload identity, capability hash and timestamps. Three
`SECURITY INVOKER` RPCs are executable only by `service_role`; no client can
list/read tables. RLS is enabled as additional protection. Authenticated desktop
requests use an independent random capability; the ID appearing in CSV is not
a credential. Deletion locks the participant, removes its packages and leaves
an unlinked UUID tombstone so stale uploads/registrations cannot resurrect data.

## Local preparation

Use a current Supabase CLI (local files here were generated with **2.122.0**).
Check commands with `supabase <command> --help` for the version you install.
The migration filename was created by `supabase migration new research_storage`.
Do not recreate it manually or regenerate its timestamp on each deployment.

```powershell
python tools/service_schema.py
python -m pytest -q
node --test supabase/tests/*.test.mjs
```

For real PostgreSQL privilege/transaction tests without Docker, install the
dev-only package `@electric-sql/pglite@0.5.11` outside the add-on and set
`PGLITE_TEST_MODULE` to its absolute `dist/index.js` path, then run:

```powershell
node --test supabase/tests/*.test.mjs
```

Node 24 was used for these tests. PGlite is a PostgreSQL WASM runtime, not a mock
SQL parser. It tests roles/RLS, invoker RPCs, ownership, duplicate receipts,
deletion and rejected stale writes. Hosted Edge routing/credentials and actual
Oura accounts still need a deployment pilot. No test sends real health data.

## Deployment sequence

1. Create the dedicated Supabase project in the chosen region. Retain project
   admin access only for authorized operators. Enable MFA on operator accounts.
2. Publish the completed policy and study information. Fill a local copy of
   `supabase/.env.example`, leaving all three enable flags false initially. Do not
   commit it. Generate `OAUTH_STATE_SECRET` from at least 32 random bytes,
   base64url encoded. `SERVICE_URL` must be the exact public function root.
3. Register the Oura app with the fixed `/oauth/callback` URL. Put its client ID
   and secret only in server environment secrets. Use the notice hash printed
   by `python tools/service_schema.py`; if you customize the notice, use
   `python tools/service_schema.py --config path/to/deployed-addon-config.json`
   to generate the matching hash rather than retaining the default. Include
   the study/operator and published policy URL in that deployed notice text.
   For Google Health, use the separate `/google/oauth/callback` URL and
   `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`; follow [GOOGLE_HEALTH.md](GOOGLE_HEALTH.md).
4. Link the CLI to this exact project, review the migration, then apply it:

   ```powershell
   supabase link --project-ref YOUR_PROJECT_REF
   supabase db push --dry-run
   supabase db push
   supabase secrets set --env-file supabase/.env
   supabase functions deploy qs-service
   ```

   `SUPABASE_URL` and server credentials are supplied by hosted functions. The
   handler prefers the default modern key from `SUPABASE_SECRET_KEYS` and falls
   back to `SUPABASE_SERVICE_ROLE_KEY`. It sends `apikey` and, for legacy JWT
   keys, `Authorization` to the fixed project REST endpoint. Never copy either
   key to the desktop.
5. `verify_jwt=false` is intentional: the browser OAuth callback has no Supabase
   user JWT. It does **not** make private actions unauthenticated. Research
   routes verify the capability, and token routes rely on signed state, PKCE or
   the provider credential itself. There is no CORS API for arbitrary browser apps.
   Verify anon/authenticated REST calls to all three RPCs are denied and both
   private tables are inaccessible. Do not expose `qs_private` in Data API
   settings or grant clients access as a workaround.
6. Configure gateway/WAF rate controls for public OAuth start/refresh and
   participant registration. Set a per-IP request limit, request-size limit,
   alert on unusual registrations, database/egress usage and billing; review
   against the anticipated pilot size. Shared database quotas now bound admitted
   requests and stored research packages; see [BACKEND_OPERATIONS.md](BACKEND_OPERATIONS.md)
   for exact limits, telemetry, operator reports and recovery. OAuth bodies are
   limited to 16 KiB, uploads to 8 MiB and daily records to 36,600. These controls
   are not an infrastructure billing ceiling. Configure
   operator retention and backup restore/deletion procedures. Keep tokens,
   payloads, authorization headers and callback query strings out of logs;
   hosting access logs may otherwise retain URL/IP metadata.
7. Keep all three enable flags false for the current desktop beta. Only after a
   separate launch decision, set `OURA_ENABLED=true` for the permitted pilot. Set the desktop add-on
   `services.base_url` to this function root. Verify the complete live flow with
   a consenting test account. Enable `RESEARCH_ENABLED=true` only after the
   policy, recipient and approved research-storage arrangements are ready.
   Deletion remains available even when new uploads are disabled.
   Google access has an independent `GOOGLE_HEALTH_ENABLED` switch; keep it false
   until its policy, verification/research requirements and permitted pilot are ready.
   Update the closed-state monitor expectation only as part of that launch.
8. Complete [OURA_APPROVAL.md](OURA_APPROVAL.md) before broad distribution.
   Publish a configured add-on only with the intended public service URL.

Track desktop checks and the separate online launch gates in
[BETA_CHECKLIST.md](BETA_CHECKLIST.md). The deployed monitor is private, checks
all three flags every five minutes, and does not send operator notifications.
Arrange independent uptime/alert delivery before an online pilot.

The checked-in desktop URL points to the disabled deployment. Local Anki
analysis and CSV work independently. No client secret or shared bearer credential
is bundled. The Linux desktop needs `secret-tool`/Secret Service; macOS uses
Keychain and Windows uses DPAPI. There is no plaintext token fallback. Only
Windows credential storage has been exercised on the present test host.

DPAPI credentials are tied to the Windows account/machine protection context;
copying a profile is not a portable credential backup. Restore access in the
original OS context or remove the unreadable `connections.dpapi` file and
reconnect. Lost research credentials require the operator-assisted deletion
process described in the policy; a CSV participant ID alone grants no access.

## Operating research storage

Use the Supabase SQL editor with an authorized operator role to inspect/export
`qs_private` data. `supabase/research-export.sql` produces one daily JSON record
with metadata and submission timestamps, selecting the latest submitted snapshot
for each public research participant/day. Preserve raw immutable submissions
for reproducibility under the published retention criteria. A formation score can
change as outcomes arrive; repeated uploads are not independent samples.

Research extraction should remain outside the public Edge endpoint. Do not add
a public list/download endpoint or expose tables just to export a study dataset.
Apply deletion requests to research extracts and replay deletions after restoring
backups. Tombstones must survive restoration/replay; live-row deletion alone is
not a promise of immediate physical deletion from backup media.

Pending deletion retries run while Anki is open, including if the public service
URL changes. A deletion request uses the original saved endpoint. Lost local
credentials need an operator-assisted recovery process. Research upload failures
require the user's explicit Send again; background tasks never retry a research
upload. OAuth token rotation and daily CSV publication continue independently.

The backend transports tokens and explicitly submitted aggregates, not the
daily Oura/Google sleep or step API responses. Normal server bandwidth is therefore small;
measure actual invocation/storage/egress usage in the pilot. Plan limits,
project pausing, logs and pricing change—check the current Supabase dashboard.

## Verified platform references

[Function configuration](https://supabase.com/docs/guides/functions/function-configuration),
[server secrets](https://supabase.com/docs/guides/functions/secrets),
[API keys](https://supabase.com/docs/guides/api/api-keys),
[RLS](https://supabase.com/docs/guides/database/postgres/row-level-security),
[changelog](https://supabase.com/changelog).
The October 2026 TLS notice affects old CBC-only clients; the add-on uses modern
Python/OpenSSL HTTPS defaults and does not weaken certificate/TLS validation.
# Pending enrollment/lifecycle deployment (0.19.0)

The source now requires package schema v5, separate enrollment and sharing notice
v6. Apply both 20261102 research migrations and deploy the matching handler while
keeping all three feature flags false. The prepared protocol remains unapproved
and enrollment closed. `supabase/deployment.json` continues to describe the older
hosted deployment until a live deployment is performed and verified. See
[RESEARCH_CONTROLS.md](RESEARCH_CONTROLS.md) for operator procedures and remaining
signature-method/approval checks. The new closed-state verifier requires
`research_controls_version=1`; the old deployment will not satisfy that check.
