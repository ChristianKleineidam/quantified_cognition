"""The unattended verifier must reject misleading packages and missing prerequisites."""

import json
from zipfile import ZipFile, ZipInfo

import pytest

from tools import verify


def package(root, *, extra=None, identity="quantified_self_memory", description=b"Description",
            metadata_version="0.23.0rc1", filename="quantified-cognition-0.23.0-rc.1.ankiaddon",
            timestamp=(1980, 1, 1, 0, 0, 0)):
    (root / "dist").mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs/ANKIWEB_DESCRIPTION.txt").write_bytes(b"Description")
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{metadata_version}"\n')
    with ZipFile(root / "dist" / filename, "w") as archive:
        def write(name, value):
            archive.writestr(ZipInfo(name, date_time=timestamp), value)

        write("manifest.json", json.dumps({
            "name": "Quantified Cognition", "package": identity,
            "human_version": "0.23.0-rc.1",
        }))
        write("docs/ANKIWEB_DESCRIPTION.txt", description)
        write("__init__.py", "value = 1\n")
        if extra:
            write(*extra)


def test_archive_preserves_identity_and_exact_description(tmp_path):
    package(tmp_path)
    assert verify.inspect_package(tmp_path)["entries"] == 3
    package(tmp_path, identity="new_profile_identity")
    with pytest.raises(RuntimeError, match="identity"):
        verify.inspect_package(tmp_path)
    package(tmp_path, description=b"Outdated")
    with pytest.raises(RuntimeError, match="description"):
        verify.inspect_package(tmp_path)


@pytest.mark.parametrize("name,value", [
    ("../.env", "secret"), ("profile/collection.anki2", "private"),
    ("scores.csv", "private"), ("broken.py", "def invalid("),
])
def test_archive_rejects_private_unsafe_or_uncompilable_files(tmp_path, name, value):
    package(tmp_path, extra=(name, value))
    with pytest.raises((RuntimeError, SyntaxError)):
        verify.inspect_package(tmp_path)


@pytest.mark.parametrize("options,message", [
    ({"metadata_version": "0.22.0"}, "versions differ"),
    ({"filename": "unidentified.ankiaddon"}, "filename"),
    ({"timestamp": (2026, 11, 8, 0, 0, 0)}, "variable ZIP timestamps"),
])
def test_archive_rejects_mismatched_release_metadata(tmp_path, options, message):
    package(tmp_path, **options)
    with pytest.raises(RuntimeError, match=message):
        verify.inspect_package(tmp_path)


def test_missing_database_runtime_is_an_error_not_a_skipped_suite(tmp_path, monkeypatch):
    monkeypatch.setattr(verify.importlib.metadata, "version", lambda name: "26.9.3")
    with pytest.raises(RuntimeError, match="PGLITE_TEST_MODULE"):
        verify.preflight(tmp_path, "node", tmp_path / "missing.js")


def test_github_actions_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(verify.importlib.metadata, "version", lambda name: "26.9.3")
    pglite = tmp_path / "index.js"
    pglite.touch()
    workflow = tmp_path / ".github/workflows"
    workflow.mkdir(parents=True)
    (workflow / "test.yml").write_text("on: push\n")
    with pytest.raises(RuntimeError, match="GitHub Actions"):
        verify.preflight(tmp_path, "node", pglite)
