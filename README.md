# Quantified Cognition

**Discover how your ability to learn varies from day to day.**

Quantified Cognition turns your existing Anki history into daily estimates of
memory formation and recall. Use these to explore personal experiments with
study routines, sleep, supplements and other changes, using the studying you
already do.

[Project website](https://quantifiedcognition.jollyjoyjourney.com/) ·
[Install from AnkiWeb](https://ankiweb.net/shared/info/1278210131) ·
[GitHub releases](https://github.com/ChristianKleineidam/quantified_cognition/releases/latest)

## Get started

Requires desktop Anki with FSRS enabled in Deck Options. See the release notes
for supported Anki versions. Windows and Linux have been tested; macOS has not
yet been verified.

1. In Anki, open **Tools → Add-ons** and click **Get Add-ons…**.
2. Enter code **`1278210131`**, install the add-on, and restart Anki.
3. Open **Tools → Quantified Cognition…**.

You can also download a `.ankiaddon` file from GitHub releases and use
**Install from file**.

The report shows your full study history and checks for new data in the
background, including pre-FSRS reviews and reviews synced from mobile or AnkiWeb.
Choose a metric, click a point to inspect a day, or open **Daily table**.
**Custom dates** lets you focus on a particular period.

## Understand your scores

Anki's statistics describe how much you studied and how often you answered
correctly. **Memory formation** estimates how effectively you learned on an
earlier day. If you study a card on Monday and review it on Friday, Friday's
answer contributes to Monday's formation score. Comparing later answers with
Anki's FSRS predictions helps account for differences between cards and review
intervals.

**Recall** describes how you performed that day, using the first attempt on each
card. Both metrics are shown overall and separately for intervals of **up to
30 days** and **over 30 days**. Recall uses the time since the previous review;
formation uses the time until the next review.

Positive scores mean more successful recall than predicted; negative scores mean
less. For example, 90% recalled against an 85% prediction gives **+5 percentage
points**. Scores include counts and approximate uncertainty ranges. **On par**
means no clear difference from the prediction; **insufficient data** means there
isn't enough evidence for a comparison.

Formation scores mature as cards are reviewed again. Check follow-up coverage:
missing outcomes stay pending, and uncertainty ranges cover only observed
outcomes. Changing your current FSRS settings can also revise historical scores.

These experimental measurements have not been validated for detecting changes in
everyday cognitive performance. Use them alongside other evidence, rather than
as the sole basis for major or medical decisions. Narrow ranges do not rule out
bias, and correlations do not establish cause and effect. See the
[Methods page](https://quantifiedcognition.jollyjoyjourney.com/methods/).

## Connect sleep and activity

Open **Health data… → Connect Oura…** and sign in. Allow **Daily** for sleep and
steps, and **Workout** for workouts. Either or both permissions work. Initial
sync imports your available history; later updates run automatically while Anki
is open. **Sync now** checks immediately.

**Personal insights…** compares total/light/deep/REM durations, stage percentages,
naps, steps and workouts with your scores. Formation uses sleep before and after
studying separately; recall uses preceding sleep. Main sleep and naps stay
separate. **Stage comparisons** compares stages at the same total sleep duration.

Insights show uncertainty and insufficient-data labels. Return after weeks or
months: **Since your last visit** shows added history, newly available formation
outcomes and changes in uncertainty.

## Export your data

In **Export CSV…**, choose **Save a copy…** for a snapshot of the dates shown.
For other programs to read your results automatically, enable **Keep a CSV updated
automatically** and choose a fixed file location. Settings save immediately.

Automatic exports contain the full saved history, including Oura summaries. They
update in the background after sync and completed review queues, and can finish
after Anki's window closes. If the destination is unavailable, the previous CSV
stays intact and export is retried. Keep dated copies to preserve earlier analyses.

## Privacy and research participation

Analysis runs locally without changing your study schedule. Add-on data stays in
your Anki profile, separately from the collection, and is not synced by AnkiWeb.
Health data is encrypted locally; exported CSVs remain readable. Back up add-on
data and CSVs separately. See [encryption and recovery](docs/ENCRYPTION.md).

Personal features work without research participation. The first sharing prompt
records whether you expressed interest before seeing results; you can change
your choice under **Research sharing…**. Joining requires explicit consent;
every upload also requires a package preview and confirmation.

Contributions include daily scores, pseudonymous individual review predictions
and outcomes, saved Oura summaries and measurement metadata. Card text, deck
names, original Anki IDs and exact event times are excluded. Oura contributions
also send an access token, stored encrypted, to check continued permission while
Anki is closed.

Use **Delete uploaded data…** to request deletion. Uninstalling does not withdraw
a previous contribution. Read the
[research information](https://quantifiedcognition.jollyjoyjourney.com/research/)
and [privacy policy](https://quantifiedcognition.jollyjoyjourney.com/privacy/)
before participating.

## Help and contributing

[Report problems or suggest improvements](https://github.com/ChristianKleineidam/quantified_cognition/issues).
Include your Anki/add-on versions and reproduction steps. Keep personal
collections, health data, CSVs and credentials out of public reports.

For building and testing, see [CONTRIBUTING.md](CONTRIBUTING.md).
Licensed under [AGPL-3.0-or-later](LICENSE).
