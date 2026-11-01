"""The unattended verifier must reject misleading packages and missing prerequisites."""

import json
from zipfile import ZipFile

import pytest

from tools import verify


def package(root, *, extra=None, identity="quantified_self_memory", description=b"Description"):
    (root / "dist").mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs/ANKIWEB_DESCRIPTION.txt").write_bytes(b"Description")
    with ZipFile(root / "dist/test.ankiaddon", "w") as archive:
        archive.writestr("manifest.json", json.dumps({
            "name": "Quantified Cognition", "package": identity,
        }))
        archive.writestr("docs/ANKIWEB_DESCRIPTION.txt", description)
        archive.writestr("__init__.py", "value = 1\n")
        if extra:
            archive.writestr(*extra)


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
