# Bundled encryption library

PyCryptodomex 3.24.1 is bundled so health encryption works without downloading or
installing dependencies into Anki's Python. The five CPython stable-ABI wheels
cover Windows x64/ARM64, macOS Intel/Apple Silicon, and Linux x64/ARM64.

`manifest.json` pins every wheel's SHA-256 from its versioned PyPI release.
The loader verifies the selected wheel before extracting its `Cryptodome`
package into the add-on's local runtime cache. These are the upstream wheels,
without source modifications. Their included distribution metadata contains the
BSD/Public Domain licensing notices. The add-on archive retains those notices
inside each wheel.

Source: <https://github.com/Legrandin/pycryptodome/tree/v3.24.1>

Release: <https://pypi.org/project/pycryptodomex/3.24.1/>

To reproduce this directory, run `python tools/vendor_crypto.py` from the
repository root. No private encryption key belongs in this directory or package.
