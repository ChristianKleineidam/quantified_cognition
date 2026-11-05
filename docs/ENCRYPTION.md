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
All three feature flags remain false and prepared protocols remain unapproved.

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
latest received snapshot per participant/day. It preserves submission and
consent provenance. All packages must validate before a plaintext analysis file
is created; an existing output is not overwritten. The resulting file is a
research extract covered by the registered inventory and deletion process.
Decompression is limited to 64 MiB before JSON parsing; encrypted compressed
content must fit the existing 8 MiB upload body limit. This accommodates long
daily histories without expanding the accepted scientific fields.

Encryption does not remove provider approval, research consent, retention review
or copy-deletion obligations. No private key is sent to a hosted API or used in
automated CI. Key rotation requires a matching add-on public key, schema metadata
and database admission migration, plus verification before enabling uploads.
