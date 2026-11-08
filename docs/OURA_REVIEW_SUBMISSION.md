# Quantified Cognition Oura review submission

## Application details

| Field | Value |
| --- | --- |
| App | Quantified Cognition |
| Operator | JollyJoyJourney |
| Support contact | quantifiedcognition@jollyjoyjourney.com |
| Website | https://quantifiedcognition.jollyjoyjourney.com/ |
| Privacy policy | https://quantifiedcognition.jollyjoyjourney.com/privacy/ |
| Terms of Use | https://quantifiedcognition.jollyjoyjourney.com/terms/ |
| Participant information | https://quantifiedcognition.jollyjoyjourney.com/research/ |
| OAuth redirect | `https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service/oauth/callback` |
| Scopes | `daily`, `workout` |
| Previous review | Ticket #8471927. Oura confirmed the active test account. |

## Text for the review request

Can you approve Quantified Cognition to connect more than 10 Oura users and allow voluntary uploads of daily Oura summaries for JollyJoyJourney’s memory research?

We want to learn how Oura-measured sleep and activity relate to what people remember days or weeks after studying. Quantified Cognition uses later Anki reviews to estimate retention. Daily provides sleep stages, naps and steps. Workout provides exercise timing, duration and intensity. Personal analysis runs locally, and users can export a CSV.

Research participants give separate consent and review each package before sending. Pseudonymous daily summaries and Anki scores are encrypted on-device and stored in Frankfurt. Raw records and exact event times stay out of uploads. Participants can withdraw and request deletion. Collaborator access requires Oura’s permission.

Oura confirmed the active test account in ticket #8471927.

## Approval received

On **6 November 2026 at 19:47 UTC**, Ken from Oura's Enterprise Support Team
confirmed that the developer application was approved and the ten-user limit
was lifted, in ticket **#8481236**. This followed the combined request in
**#8471927** below. Continued API use is subject to the API Agreement and branding
conditions. [Approval record](OURA_APPLICATION_APPROVAL.json)

Research enrollment and uploads remain disabled; recording this approval changes
neither the service switches nor the research protocol's closed state.

## Submission history

Resubmitted on **6 November 2026** through **Request more users**. The Oura Developer Portal showed **In Review**. The portal did not provide a message field, so the review text was sent in ticket #8471927, where Oura confirmed the active test account. [Oura Developer Portal](https://developer.ouraring.com/applications)

The pilot evidence and synthetic walkthrough in `dist/oura-review/` are available if Oura asks for more detail. [Pilot results](OURA_PILOT_RESULT.json) · [Technical notes](OURA_APPROVAL.md)
