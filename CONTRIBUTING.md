# Contributing to Quantified Cognition

For a bug report, include the Anki and add-on versions, reproduction steps,
expected behavior and sanitized errors. Use synthetic examples when possible.
Keep personal collections, health records, research contributions and credentials
out of issues, patches and test fixtures.

## Set up a development environment

Use Python 3.10 or later and create a virtual environment:

```sh
python -m venv .venv
```

Activate it with `.venv\Scripts\Activate.ps1` in PowerShell or
`source .venv/bin/activate` in a POSIX shell, then install the development
dependencies:

```sh
python -m pip install -r requirements-dev.txt
```

These dependencies include Anki and Qt. Normal add-on users install the packaged
release and do not need this environment.

## Run checks and build

```sh
python -m pytest
python -m ruff check addon tests tools
python tools/build_addon.py
```

The build writes a versioned `.ankiaddon` file in `dist/`. Install it through
Anki's **Tools → Add-ons → Install from file** in a disposable profile with
synthetic cards.

The Python suite covers score calculations, statistical simulations, UI,
background updates, sync and CSV recovery using synthetic collections. To also
run the Node/PostgreSQL backend tests and package checks, install Node 22+ and
PGlite 0.5.11, then run:

```sh
python tools/verify.py --pglite /absolute/path/to/pglite/dist/index.js
```

On Linux, native PostgreSQL recovery/concurrency checks can be included with
`--postgres-bin /absolute/path/to/postgresql/bin`; these also require `age` and
`age-keygen` on PATH. Run verification in a disposable development environment;
local execution uses your current permissions and network.

## Further reading

- [Desktop beta checklist](docs/BETA_CHECKLIST.md)
- [Research design](docs/RESEARCH_DESIGN.md)
- [Statistical validation and simulations](docs/STATISTICAL_VALIDATION.md)
- [Personal insights methods](docs/INSIGHTS.md)
- [Encryption and recovery](docs/ENCRYPTION.md)

The project is licensed under [AGPL-3.0-or-later](LICENSE).
