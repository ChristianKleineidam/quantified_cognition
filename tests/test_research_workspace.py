import io
import json
import uuid
from urllib.error import URLError

import pytest

from addon import vault
from tools import research_guard


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    token = tmp_path / "token.dpapi"
    token.write_bytes(b"s" * 48)
    monkeypatch.setattr(vault, "_dpapi", lambda value, decrypt=False: value if decrypt else None)
    data = {"service_url": "https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service",
            "token_file": str(token)}
    path = tmp_path / "workspace.json"
    path.write_text(json.dumps(data))
    return path, data


@pytest.mark.parametrize("url", [
    "http://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service",
    "https://attacker.example/functions/v1/qs-service",
    "https://wfjlzecckihsgcvauqwt.supabase.co:8443/functions/v1/qs-service",
    "https://user@wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service",
    "https://wfjlzecckihsgcvauqwt.supabase.co/functions/v1/qs-service?forward=1",
])
def test_workspace_rejects_changed_destination(workspace, url):
    path, data = workspace
    path.write_text(json.dumps({**data, "service_url": url}))
    with pytest.raises(ValueError):
        research_guard.configured_authorization(path)


@pytest.mark.parametrize("raw,allowed", [
    (b'{"allowed":true}', True), (b'{"allowed":false}', False),
    (b'{"allowed":1}', False), (b'{"allowed":true,"extra":1}', False),
    (b"invalid", False), (b" " * 4097, False),
])
def test_guard_requires_bounded_explicit_authorization(workspace, monkeypatch, raw, allowed):
    path, data = workspace
    class Reply(io.BytesIO):
        status = 200
    class Transport:
        def open(self, req, timeout):
            assert req.full_url == data["service_url"] + "/internal/research-use"
            assert req.headers["Authorization"] == "Bearer " + "s" * 48
            assert timeout == 5
            return Reply(raw)
    monkeypatch.setattr(research_guard.request, "build_opener", lambda handler: Transport())
    assert research_guard.allowed_extract(uuid.uuid4(), config_path=path) is allowed


def test_guard_transport_failure_and_redirects_fail_closed(workspace, monkeypatch):
    path, _ = workspace
    class Transport:
        def open(self, *_args, **_kwargs):
            raise URLError("synthetic secret must not be logged")
    monkeypatch.setattr(research_guard.request, "build_opener", lambda handler: Transport())
    assert research_guard.allowed_extract(uuid.uuid4(), config_path=path) is False
    assert research_guard.NoRedirect().redirect_request(None) is None
