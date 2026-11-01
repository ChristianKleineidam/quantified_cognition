# Automated verification on Silentworker

This project uses Silentworker directly, with no GitHub Actions workflows or
Actions runner registration. The dedicated `quantified-cognition-tests.timer`
checks the private repository's `main` branch five minutes after the preceding
pass finishes, plus up to 30 seconds of jitter. It runs after server startup too.

A new commit runs Ruff, all Python tests (including formation/phase statistical
simulations and native Anki/Qt tests), all Node backend/PostgreSQL tests, and the
add-on build and archive checks. Missing Qt or PGlite fails verification rather
than silently omitting those suites. Windows-specific file-lock tests are
expected to skip on Debian; this service does not replace Windows testing,
official-installer/AnkiWeb-sync walkthroughs, or macOS testing.

An unchanged commit reuses its result. A failed result remains a failure on
subsequent polls; another commit or an explicit forced run is required to retry.
Fetch errors fail the service without replacing the last completed test result.
Only the five newest run directories are retained. No GitHub comments, statuses,
emails or automatic source changes are published by the service.

## Execution boundary

The root-owned controller is installed at
`/opt/quantified-cognition-tests/poll.py`. It uses a repository-specific **read-only**
deploy key in `/etc/quantified-cognition-tests/`, pins GitHub host keys from
GitHub's HTTPS metadata, and fetches only `main` into a bare repository. It never
checks out or executes fetched code as root. Source archives reject links,
special files and traversal, with bounded file counts and sizes.

Each revision runs as the separate `qctests` system user in a transient systemd
service. The executor has no GitHub/provider/research credentials, no home
directory access, no host network connection and no write access outside its
source directory and private temporary directory. Loopback remains available
inside its private network for synthetic OAuth/HTTP tests. It receives a fixed
environment and root-owned test runtimes. Dependencies are installed by the
operator, never automatically from a new repository revision.

The executor is limited to 75% of one CPU, 1,250 MiB RAM, 512 MiB swap and 45
minutes, with low CPU/I/O priority. Backend test files run sequentially. The
controller has a separate 256 MiB limit. The service reads no real Anki profiles
or health data and makes no cloud deployment or feature-setting changes.

## Results and manual runs

On Silentworker:

```bash
sudo cat /var/lib/quantified-cognition-tests/status.json
sudo cat /var/lib/quantified-cognition-tests/poll.json
sudo systemctl status quantified-cognition-tests.timer --no-pager
sudo journalctl -u quantified-cognition-tests.service -n 30 --no-pager
```

`status.json` records the tested commit, result and retained log path. `poll.json`
records the latest fetch/check result, including fetch failures. Each run also
contains `result.json`, `run.log`, the synthetic source workspace, and
`source/.reference/verification/report.json` plus the Python JUnit XML. A running
status is not a pass. If a run is killed, inspect the service result and log.

Run immediately through the normal service:

```bash
sudo systemctl start quantified-cognition-tests.service
```

Explicitly retry an unchanged revision:

```bash
sudo python3 /opt/quantified-cognition-tests/poll.py --force
```

The controller lock prevents overlapping runs. The installed timer and service
are registered in Silentworker's existing health inventory, so failed tests or
a stopped timer appear through its normal health reporting. This adds no new
notification destination.

## Installation and maintenance

Copy `tools/silentworker/` to the server. Run `install.py --prepare-only` as root;
register the printed **public** key as a read-only deploy key on
`ChristianKleineidam/quantified_cognition`. The private key stays on Silentworker.

Run the installer as root with `--node-source /absolute/path/to/node`, pointing
to an existing Node 22+ binary. It copies the binary into the dedicated runtime,
so subsequent execution does not depend on the source installation. It installs
the pinned Python requirements and PGlite 0.5.11, verifies the npm tarball's
recorded SHA-512 integrity, records the runtime versions/hash and full Python
dependency snapshot, and installs the systemd units. Keep root ownership of
the installed controller, runtimes and configuration. Native Qt shared libraries
must also be available on the host.

Run and inspect the first verification, then rerun installation with `--enable`
to register health checks and enable the timer. Existing health-inventory edits
are preserved and a backup is saved before appending this workload's entries.
Controller/unit or dependency changes require explicitly rerunning installation;
the privileged controller never updates itself from fetched source.

To pause verification:

```bash
sudo systemctl disable --now quantified-cognition-tests.timer
```

Update the health inventory when intentionally removing this workload. Pausing
the timer does not interrupt a run already executing.

For an equivalent local check in a clean source checkout, install
`requirements-dev.txt`, Node 22+ and PGlite 0.5.11, then run:

```bash
PGLITE_TEST_MODULE=/absolute/path/to/pglite/dist/index.js python tools/verify.py
```

Local execution uses the current user's permissions and network; only the
Silentworker systemd executor supplies the isolation described above. Larger
held-out simulations remain reproducible with the separate command in
`STATISTICAL_VALIDATION.md`; the ordinary suite already includes seeded
calibration gates on every new commit.
