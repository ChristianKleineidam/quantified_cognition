# Local caches and research encryption

Version 0.20.0 uses PyCryptodomex 3.24.1, pinned and bundled for the supported
desktop platforms. Normal use installs no Python packages and makes no dependency
downloads. The library's upstream licence stays inside each bundled wheel.

## Local health data

`health.sqlite3`, personal-insight summaries, visit summaries and pending research
packages use AES-256-GCM with a fresh random nonce per write. File purpose is
authenticated too. SQLite runs in memory; only an authenticated encrypted snapshot
is written to disk. Temporary cache files also contain ciphertext. An unchanged
database snapshot is not rewritten. Reads and writes run on the existing file
workers, with a profile health lock independent of Anki's collection queue.

A random profile key is stored through Windows DPAPI, macOS Keychain or Linux
Secret Service. It is separate from provider credentials. The profile's
`.health-encryption-present` marker prevents replacing a lost key silently.
An unavailable key or failed integrity check preserves the saved file and reports
an error. Keep the original OS credential store when migrating profiles; copying
cache files alone is insufficient to unlock them. A lost key requires restoring
it or explicitly removing and reimporting the affected cache.

Legacy SQLite and JSON caches are migrated on their next successful access.
Migration reads existing SQLite data into memory, atomically replaces the managed
file with ciphertext, and removes obsolete SQLite sidecars. A failed migration
preserves its input. This does not erase earlier filesystem snapshots, outside
backups or previously deleted disk blocks. Close older add-on versions before
upgrading; they cannot read encrypted caches.

User-selected CSV and other analysis exports remain readable files at the chosen
location, including the automatic fixed CSV used by other programs. Encryption
does not change FSRS reuse, CSV retry scheduling or the original sharing audit.

## Research measurements

The app validates the aggregate allowlist, compresses the package, then creates an RFC 9180 HPKE envelope
using X25519, HKDF-SHA256 and AES-256-GCM. The recipient public key and its
SHA-256 fingerprint are bundled. The app checks the service's advertised
fingerprint against its bundled key; it never accepts a replacement key from the
server. The envelope header is authenticated. A pending preview stores both its
readable contents and the once-encrypted envelope inside the encrypted local
cache. Retries resend that same envelope and submission ID.

Only the encrypted measurements are sent. The server also receives the random
participant/enrollment and submission IDs, notice version/hash, submission time,
declared providers, daily-row count, algorithm, key ID and ciphertext. Consent and
deletion records remain separately readable so the server can enforce eligibility,
current enrollment, protocol permissions, retention reviews, quotas, retry
deduplication and withdrawal. It cannot inspect or validate encrypted scientific
values. Provider declarations are checked against protocol permissions and must
be verified again against the decrypted package before analysis.

The Edge handler rejects plaintext submissions. Database API writers must also
use valid envelopes with the current key ID. Historical plaintext fixtures can
still be read by a database operator; no real plaintext research submissions
existed when encryption was introduced. Existing release/withdrawal inventory
continues to cover encrypted downloads, recovery copies and decrypted extracts.
Oura connections are enabled with an approved API application. Google Health and
research uploads remain disabled; prepared research protocols remain closed.

Research contributions containing Oura data also register a separately encrypted
access token for authorization monitoring. The Edge-held AES-GCM key is separate
from the research decryption key. Refresh tokens are not stored on the server.
The monitoring table is excluded from both extracts and recovery archives;
restore requires fresh verification. See [revocation handling](OURA_RESEARCH_REVOCATION.md).

## Operator keys and analysis

The private key stays off Supabase and Silentworker. On the operator's computer:

```
M:/CodexSecrets/quantified-cognition/research-decryption-key.dpapi
M:/CodexSecrets/quantified-cognition/research-decryption-key.age
```

The first copy is protected by the operator's Windows account. The portable
recovery copy is encrypted to the operator's existing SSH public key; protect
the corresponding SSH private key independently. These files are not repository
or add-on inputs. Keep old private keys when rotating the public recipient key;
earlier submissions still need them.

Register a fresh extract inventory as described in [RESEARCH_CONTROLS.md](RESEARCH_CONTROLS.md).
The revised `supabase/research-export.sql` releases encrypted packages and consent
provenance. Save the SQL query's JSON result locally, then run:

```powershell
python tools/research_crypto.py decrypt --input research-download.json --output analysis.csv --private-key M:/CodexSecrets/quantified-cognition/research-decryption-key.dpapi
```

For recovery on another computer, supply the `.age` copy plus `--age <age executable>`
and `--identity <SSH private-key path>`. Decryption runs locally, authenticates
every package, checks its allowlist and header declarations, and selects the
latest received snapshot per participant/day. Package v7 also writes
`analysis-reviews.csv` with the newest complete review snapshot per participant.
Use `--reviews-output` to choose a different review CSV path. Older review snapshots
are replaced as a whole, so corrected or deleted reviews cannot reappear through
row-by-row merging. The files preserve participant, submission and consent
provenance. All packages must validate before a plaintext analysis file
is created; an existing output is not overwritten. The resulting file is a
research extract covered by the registered inventory and deletion process.
Decompression is limited to 64 MiB before JSON parsing; encrypted compressed
content must fit the existing 8 MiB upload body limit. Review snapshots are
limited to one million rows, and both byte limits also apply. Oversized packages
fail preparation with an explicit error; reviews are never silently truncated.
Compression uses zlib level 9 and runs on the background worker.

Review rows use keyed, profile-specific card pseudonyms and within-card order.
They include day, rating, binary success, reconstructed probability, elapsed seconds,
review type and eligibility flags. Missing probabilities remain null. The original
card IDs are retained only in the local Anki-derived results cache, separate from
the main collection. No original review IDs or review clock times are stored there.
The pseudonym key stays in the OS credential store and is excluded from packages.
Prediction context records that current FSRS parameters were used. Version 6
encrypted daily-only packages remain readable by the download tool.

For analysis, `prediction` is a probability from 0 to 1 and `success` is 0 for
Again or 1 for Hard/Good/Easy. The Brier score is the mean of
`(success - prediction) ** 2` over the selected records with a prediction.
Null predictions must not be replaced with zero. The values retain the native
probability precision.

`ordinal` orders the included attempts within a card; it is not a permanent
review ID. `review_type` follows Anki's 0 learning / 1 review / 2 relearning /
3 rescheduling-filtered codes. Non-rescheduling previews and administrative
entries are excluded. `episode` separates reset or unmodelled-exposure
boundaries. `first_on_day` selects recall attempts; `last_on_day` identifies
potential formation sources. `has_post_state` and `formation_blocked` identify
missing reconstructed states and blocked formation follow-ups. Elapsed seconds
describe the preceding answer, including an intervening preview exposure when
present; these relative gaps contain no absolute review timestamp.

Encryption does not remove provider approval, research consent, retention review
or copy-deletion obligations. No private key is sent to a hosted API or used in
automated CI. Key rotation requires a matching add-on public key, schema metadata
and database admission migration, plus verification before enabling uploads.
