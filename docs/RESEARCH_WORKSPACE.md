# Research workspace

The operator workspace is configured on the Windows computer at
`M:/QuantifiedCognition/Research`. `Extracts/` holds registered downloads, daily
and per-review CSVs and derived results; `Scripts/` holds analysis code and
`Records/` holds operational evidence. Its directory access is restricted to the
operator's Windows account. No participant measurements have been downloaded.

`workspace.json` names the HTTPS authorization service and a Windows DPAPI
protected token outside the workspace. This token can obtain only an
`allowed: true/false` decision for a registered extract. It cannot download data,
change enrollment, confirm deletion or enable uploads. There is no new database
login. The independent research decryption key remains off-server in the
operator's protected key directory; its public recipient was verified.

## Use

First register the copy as the authorized operator, following
[the extract procedure](RESEARCH_CONTROLS.md) and
[research-export.sql](../supabase/research-export.sql). Use a new UUID for every
release, explicit participant membership and the workspace folder as its
location reference. Save the encrypted query result in
`Extracts/<UUID>/download.json`. Do not paste participant records into agent
tools. Register collaborator and recovery copies separately before release.

Run decryption and every analysis through the workspace launcher:

```powershell
cd M:/QuantifiedCognition/Research
./Run-Research.ps1 -ExtractId <UUID> python analysis.py
```

For decryption, the launched command is:

```powershell
P:/Projects/random_projects/anki-quantified-self/.venv/Scripts/python.exe `
  P:/Projects/random_projects/anki-quantified-self/tools/research_crypto.py decrypt `
  --input Extracts/<UUID>/download.json --output Extracts/<UUID>/daily.csv `
  --private-key M:/CodexSecrets/quantified-cognition/research-decryption-key.dpapi
```

Run that command **through `Run-Research.ps1`**, too. It also produces
`daily-reviews.csv`. Keep derived files inside the inventoried extract directory.
The decryptor validates all packages and refuses to overwrite existing exports.

The guard checks before starting, at five-second intervals and on completion.
Authorization failure, withdrawal, a retention/access hold or connectivity
failure stops its process tree. Requests time out after five seconds. Recovery
copies cannot be used for analysis. Keep interactive notebooks under the guard
and close affected sessions when access stops.

For Brier scores use reviews with a non-missing `prediction` and calculate the
mean of `(prediction - success)^2`. These predictions are reconstructed using
current FSRS settings. Choose and record the review subset, use only the newest
complete snapshot per participant, and account for repeated cards/days when
estimating uncertainty. Do not treat missing predictions as zero.

## Withdrawal and continuing use

Inspect [the operations report](../supabase/research-operations-report.sql) for
retention reviews and deletion tasks. The server checks consent, access, review
deadlines and current Oura authorization for each use decision, including while
Anki is closed. It cannot erase an offline desktop or unregistered copy.

Stop affected work, remove the affected data or entire extract directory, and
only then have the authorized operator call
`qs_governance.confirm_copy_deletion` with actual evidence. The workspace token
cannot make that attestation. A deletion receipt stays incomplete until every
registered copy, including recovery copies, is confirmed. Do not renew a review
without documenting the continuing research need.

## Verification

The [8 November synthetic hosted rehearsal](research-e2e-2026-11-08.json) used the
actual enrollment/upload code, OS-protected stores, production envelope format,
off-server decryption key and workspace guard. It verified lost-response retries,
a known Brier score, independent scheduled revocation, process shutdown and
physical file removal before final deletion acknowledgment. Provider responses
were simulated. The temporary endpoint, schemas, roles, scheduler and secrets
were removed. Independent encrypted-backup/restore verification remains documented
in [BACKEND_RECOVERY.md](BACKEND_RECOVERY.md).
