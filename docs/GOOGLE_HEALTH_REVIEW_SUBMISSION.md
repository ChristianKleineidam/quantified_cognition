# Quantified Cognition Google Health review submission

Draft for personal-use OAuth verification. The live Google Health and research upload switches are off. This packet has not been submitted.

## Google Auth Platform fields

| Field | Value |
| --- | --- |
| App name | Quantified Cognition |
| Operator | JollyJoyJourney |
| Audience | External |
| Public support and review contact | quantifiedcognition@jollyjoyjourney.com |
| App home page | https://quantifiedcognition.jollyjoyjourney.com/ |
| Privacy policy | https://quantifiedcognition.jollyjoyjourney.com/privacy/ |
| Terms of Use | https://quantifiedcognition.jollyjoyjourney.com/terms/ |
| Authorized domain to verify | `jollyjoyjourney.com` |
| OAuth client | Web application for the hosted OAuth service used by the Anki desktop add-on |
| Production redirect | Pending. Register a Google-specific HTTPS callback on the verified domain. |
| Verification video URL | Pending. Record the real Google OAuth grant and use of both scopes. |

Use an eligible Google identity from the project for the consent screen's support email picker if it does not accept the public support address. Configure a monitored developer contact. The public home page links to the privacy policy, which includes the Google Health Limited Use commitment.

## Copy for the reviewer

Quantified Cognition is a desktop add-on for Anki, a spaced-repetition study app. It calculates how recall and memory formation vary over time. Users can connect Google Health to see their sleep stages, naps and daily steps in Anki and explore how they relate to their study results. The add-on shows how much history each comparison uses and how uncertain the estimate is. We are requesting access for these personal insights.

**`https://www.googleapis.com/auth/googlehealth.sleep.readonly`**

We use the sleep permission to read individual sleep sessions and their reported stage durations. Session timing lets us distinguish main sleep from naps and match sleep before and after studying to Anki results. The add-on shows total sleep and the available stage durations, including light, deep and REM. Daily totals alone cannot distinguish those periods or place them before or after study. Read access is sufficient.

**`https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`**

We use the activity permission to request daily step totals through the Google Health steps rollup. The add-on displays those totals and compares them with recall and memory formation. Google Health does not list a steps-only read scope. Although this permission covers other activity and fitness records, the add-on only requests the daily step rollup. Read access is sufficient.

Users can allow either permission on its own. The add-on fetches the approved dataset and leaves the other one blank. It receives health measurements directly from Google over HTTPS, analyzes them on the user's computer and stores its health cache and saved insights encrypted on that computer. Our hosted OAuth service handles authorization, token exchange, renewal and revocation. It does not receive sleep, step or Anki records in this flow. The add-on stores connection credentials in its operating system protected local vault.

Users can export daily summaries to a file they choose. The export is a readable CSV under their control. **Disconnect and delete** clears Google Health measurements and cached insights held by the add-on, clears local credentials and attempts to revoke Google access. Users can also revoke access in their Google Account. Copies they exported are managed separately. Optional research enrollment and uploads are closed. Any future use of Google-derived data for research will follow the separate Google Health research process.

## Data flow and boundaries

1. The add-on requests the two read scopes in the system browser. The hosted HTTPS OAuth service uses signed, expiring state and S256 PKCE. The browser returns to a one-time loopback listener, and the desktop exchanges the code through the service. The Web client secret is server-side. There is no Google login or access-token logging in the add-on's application code. Verify infrastructure logs for authorization codes, signed state and tokens before submitting.
2. The desktop reads `sleep/dataPoints:reconcile` with selected session and summary fields and `steps/dataPoints:dailyRollUp` with civil-day windows. The cache retains selected fields per sleep session, including timing and stage durations, and steps at the daily resolution returned by Google. It does not download intraday step samples or sleep stage intervals. The initial window is user-selectable and subsequent syncs run while Anki is open. Exact sleep session timestamps stay on the desktop.
3. Personal analyses and CSVs use the locally stored records. No research package is sent by signing in or syncing. The operator cannot use the personal connection to read a user's health measurements. Confirm the intended local cache, user-managed readable CSV and export handling with Google, including its granularity and encryption requirements.

## Before submitting

1. **Identify or set up the Cloud projects.** No dedicated project was visible under a `quantified` search in the accessible Cloud Console account. The repository records no Google OAuth credential setup or live account pilot. Use separate development and production projects. Enable Google Health API, configure Branding, Audience, the Web application client and exactly the two read scopes. Confirm their classification in Data Access. Verify `jollyjoyjourney.com` in Search Console with a project owner or editor.
2. **Use a callback on the verified domain.** The implementation currently derives its Google redirect from the shared service URL, producing `https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service/google/oauth/callback`. Google calls for ownership verification of domains used by production redirects. Give Google a dedicated redirect under `jollyjoyjourney.com` and use that exact URI in both the authorization and token exchange. Keep Oura's registered callback separate. Confirm the final routing, log handling and HTTPS behavior before recording.
3. **Close two desktop gaps before recording.** The health screen already describes the imported data, but **Connect Google Health** currently opens the browser directly. Add a dedicated disclosure immediately before browser consent with **Continue to Google** and **Cancel**. After token exchange, call `GET /v4/users/me/identity` before marking the connection active or importing. Either requested scope permits that check. Discard the returned ID, ask unlinked users to set up Google Health in the mobile app, and keep their existing local data intact if a new connection cannot be validated. Publish user help for disconnecting, revoking access and removing exported copies. Suggested wording is below.
4. **Run an isolated pilot and record it.** Use an allowlisted Google account already linked to Google Health and a disposable Anki profile. Use an isolated OAuth test endpoint and leave the production Google Health and research switches off. Verify browser return, both datasets, partial consent, refresh, and disconnect and deletion. Record the real consent flow with the same branding and scopes being verified. Keep account details, codes, tokens and private health values out of the shared recording while leaving the complete consent screen and requested permissions visible. Provide a reviewer-accessible video URL.
5. **Use Verification Center and wait for Google's instructions.** Follow the console's publishing and verification workflow for the production OAuth project when the materials are ready. The general console guide describes moving the OAuth project to Production to access **Prepare for Verification**. That setting is separate from turning on the add-on's production integration. Google Health's verification guidance describes an annual CASA security assessment for restricted scopes. Disclose that Supabase brokers tokens and Google sends health measurements directly to the desktop. Let Google confirm the applicable assessment and when to start it.

## Draft in-app disclosure and deletion help

> Quantified Cognition reads sleep sessions, sleep stages and daily steps from Google Health so you can compare them with your Anki recall and learning results. The app saves these measurements in an encrypted cache on this computer. Our connection service handles Google sign-in and token renewal. You choose whether to export a readable CSV. Research uploads are closed. Choose Continue to Google to review and allow the permissions you want to grant.

For the help page, show **Tools → Quantified Cognition… → Health data… → Google Health → Disconnect and delete…**. Explain that this removes the saved Google measurements and insights from the Anki profile. Link to [Google Account connections](https://myaccount.google.com/connections) so users can remove access directly, including after an offline disconnect. Explain that users can delete any saved CSV copies from the locations they chose.

## Demo recording script

| Shot | What Google should see |
| --- | --- |
| 1. Open the add-on | Open the health window in Anki, select Google Health and show the import start date. Show the in-app disclosure and click **Continue to Google**. |
| 2. Authorize | Show the full Google consent screen in English, with Quantified Cognition and both requested read permissions. Grant access and show the return to Anki. |
| 3. Show the uses | Show the imported sleep stages and daily step totals. Open Personal insights and show a sleep comparison and a steps comparison against the Anki scores. Use a test account with enough data for a populated table or show how the app labels limited history. |
| 4. Show controls | Show where users choose a CSV destination, then **Disconnect and delete** and the empty Google Health view. |

Synthetic previews can help frame the shots. They do not satisfy the OAuth video requirement. Existing Python and service tests use fixtures and do not establish live Google API compatibility.

## Separate research review

The proposed observational study would link pseudonymous daily Anki scores with selected daily health summaries after separate enrollment and explicit consent. The current research service stays closed. Before using Google-derived data for research, obtain independent review board approval or waiver and submit Google's research intake, supporting accreditation and exact data request with rationale. Confirm with Google how to separate the research OAuth grant from personal access and how its rules apply to previously imported history, derived data, controlled collaboration and publication. The research consent and eligibility flow must satisfy the provider's requirements before Google-derived data can be accepted by the research service.

[Google Health app verification](https://developers.google.com/health/app-verification) · [OAuth verification requirements and video](https://support.google.com/cloud/answer/13464321) · [OAuth domain requirements](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance#only_use_domains_you_own) · [Google Health developer and user data policy](https://developers.google.com/health/policies/health-api-developer-user-data-policy) · [Developer terms](https://developers.google.com/health/policies/health-api-developer-terms-and-conditions) · [Google Health setup](https://developers.google.com/health/setup) · [Health research policy](https://developers.google.com/health/policies/health-api-user-data-and-research-policy)
