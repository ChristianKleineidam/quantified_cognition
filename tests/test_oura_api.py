import threading
import time
from datetime import date
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

import pytest
from test_oura import sleep

from addon import health, oura_api, service, storage, vault
from addon.backend import UpdateCancelled
from addon.oura import parse_oura_records
from addon.research import SharingConfig
from addon.service import ServiceConfig, ServiceError

CONFIG = ServiceConfig("https://example.supabase.co/functions/v1/qs-service")


@pytest.fixture
def connection(tmp_path, monkeypatch):
    source = tmp_path / "collection.anki2"
    saved = {
        "oura": {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": time.time() + 7200,
            "service": CONFIG.base_url,
        }
    }
    monkeypatch.setattr(oura_api, "read_secret", lambda path, slot: saved.get(slot))
    monkeypatch.setattr(
        oura_api, "write_secret", lambda path, slot, value: saved.update({slot: value})
    )
    monkeypatch.setattr(oura_api, "call_service", lambda *a, **kw: {"oura_enabled": True})
    return source, saved


def test_paginated_direct_sync_minimizes_fields_and_commits_atomically(connection, monkeypatch):
    source, saved = connection
    # An upgrade backfills sessions once even if the previous add-on synced now.
    saved["oura"].update(last_sync=time.time(), last_full_sync=time.time())
    calls = []

    def request(url, *, token):
        calls.append((url, token))
        params = parse_qs(urlsplit(url).query)
        if "/sleep?" in url:
            assert "heartrate" not in params["fields"][0]
            return {
                "data": [] if "next_token" in params else [sleep()],
                "next_token": None if "next_token" in params else "second",
            }
        if "/workout?" in url:
            assert params["fields"] == [oura_api.WORKOUT_PROJECTION]
            return {"data": [], "next_token": None}
        assert params["fields"] == ["id,day,steps"]
        return {
            "data": [{"id": "never-retained", "day": "2026-09-07", "steps": 0}],
            "next_token": None,
        }

    monkeypatch.setattr(oura_api, "request_json", request)
    monkeypatch.setattr(storage, "build_report", lambda *a, **kw: pytest.fail("FSRS replay"))
    result = oura_api.sync_oura(source, CONFIG, SharingConfig())
    assert len(calls) == 4 and all(token == "old-access" for _, token in calls)
    assert parse_qs(urlsplit(calls[0][0]).query)["start_date"] == ["2000-01-01"]
    assert result.snapshot.days[date(2026, 9, 7)].values["oura_steps"] == 0
    assert saved["oura"]["last_full_sync"] > 0
    assert saved["oura"]["sleep_session_version"] == 1
    assert saved["oura"]["workout_version"] == 1
    assert oura_api.sync_oura(source, CONFIG, SharingConfig()) is None  # Hourly throttle.
    before = result.snapshot.revision
    result = oura_api.sync_oura(source, CONFIG, SharingConfig(), force=True)
    assert result.snapshot.revision == before and result.changed_records == 0
    assert b"never-retained" not in health.health_database_path(source).read_bytes()


def test_failed_later_page_and_malformed_data_do_not_replace_cache(connection, monkeypatch):
    source, _ = connection
    before = health.save_oura_import(source, parse_oura_records([sleep()], [])).snapshot

    def broken(url, **kwargs):
        if "/sleep?" in url:
            return {"data": [], "next_token": None}
        raise ServiceError("Offline", status=429, retry_after=1800)

    monkeypatch.setattr(oura_api, "request_json", broken)
    with pytest.raises(ServiceError):
        oura_api.sync_oura(source, CONFIG, SharingConfig(), force=True)
    assert health.load_health(source) == before
    monkeypatch.setattr(
        oura_api, "request_json", lambda *a, **kw: {"data": [], "next_token": "same"}
    )
    with pytest.raises(ServiceError, match="pagination"):
        oura_api.sync_oura(source, CONFIG, SharingConfig(), force=True)
    assert health.load_health(source) == before


def test_complete_window_reconciles_deletions_and_preserves_other_days(tmp_path):
    source = tmp_path / "collection.anki2"
    health.save_oura_import(
        source,
        parse_oura_records(
            [sleep(), sleep(id="old-night", day="2025-01-01")],
            [{"day": "2026-09-07", "steps": 100}],
        ),
    )
    result = health.save_oura_import(
        source, parse_oura_records([], []), authoritative_window=("2026-09-01", "2026-10-01")
    )
    assert result.changed_records == 2
    assert set(result.snapshot.days) == {date(2025, 1, 1)}


def test_refresh_rotation_is_persisted_before_cancellation(connection, monkeypatch):
    source, saved = connection
    saved["oura"]["expires_at"] = 0
    calls = []

    def refresh(config, route, data=None):
        if route == "/status":
            return {"oura_enabled": True}
        calls.append(data["refresh_token"])
        return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}

    monkeypatch.setattr(oura_api, "call_service", refresh)

    def data(*args, **kwargs):
        assert saved["oura"]["refresh_token"] == "new-refresh"
        raise UpdateCancelled("Closed")

    monkeypatch.setattr(oura_api, "request_json", data)
    with pytest.raises(UpdateCancelled):
        oura_api.sync_oura(source, CONFIG, SharingConfig())
    assert calls == ["old-refresh"] and saved["oura"]["refresh_token"] == "new-refresh"


def test_revoked_refresh_stops_automatic_retries(connection, monkeypatch):
    source, saved = connection
    monkeypatch.setattr(
        oura_api,
        "request_json",
        lambda *a, **kw: (_ for _ in ()).throw(ServiceError("Expired", status=401)),
    )
    monkeypatch.setattr(
        oura_api,
        "call_service",
        lambda *a, **kw: (_ for _ in ()).throw(ServiceError("Revoked", status=401)),
    )
    with pytest.raises(ServiceError):
        oura_api.sync_oura(source, CONFIG, SharingConfig())
    assert saved["oura"]["reauth_required"] is True
    assert oura_api.sync_oura(source, CONFIG, SharingConfig()) is None


def test_loopback_rejects_forged_state_and_accepts_single_random_path():
    callback = oura_api.Loopback()
    result = []
    worker = threading.Thread(target=lambda: result.append(callback.wait(lambda: None, 5)))
    worker.start()
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(callback.return_uri + "?state=wrong&code=evil", timeout=3)
        assert error.value.code == 400 and callback.result is None
        with urlopen(
            callback.return_uri + "?state=" + callback.state + "&code=good", timeout=3
        ) as reply:
            assert "return to Anki" in reply.read().decode()
        worker.join(3)
        assert result == ["good"]
    finally:
        callback.close()
        worker.join(5)


def test_desktop_connect_verifies_pkce_and_resets_previous_account_history(connection, monkeypatch):
    import base64
    import hashlib

    source, saved = connection
    health.save_oura_import(source, parse_oura_records([sleep()], []))
    pending, callbacks = {}, []

    def service_call(config, route, data=None):
        if route == "/status":
            return {"oura_enabled": True}
        if route == "/oauth/start":
            pending.update(data)
            return {
                "authorize_url": "https://cloud.ouraring.com/oauth/authorize?scope=daily",
                "flow": "signed-test-flow",
            }
        assert route == "/oauth/exchange" and data["code"] == "one-use-code"
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert challenge == pending["code_challenge"] and data["flow"] == "signed-test-flow"
        return {
            "access_token": "connected-access",
            "refresh_token": "connected-refresh",
            "expires_in": 3600,
        }

    def browser(url):
        def response():
            with urlopen(
                pending["return_uri"] + "?state=" + pending["state"] + "&code=one-use-code",
                timeout=5,
            ) as reply:
                assert reply.status == 200

        thread = threading.Thread(target=response)
        callbacks.append(thread)
        thread.start()
        return True

    monkeypatch.setattr(oura_api, "call_service", service_call)
    monkeypatch.setattr(oura_api, "request_json", lambda *a, **kw: {"data": [], "next_token": None})
    result = oura_api.connect_oura(source, CONFIG, SharingConfig(), open_browser=browser)
    for thread in callbacks:
        thread.join(5)
    assert not result.snapshot.days
    assert saved["oura"]["refresh_token"] == "connected-refresh"
    assert not saved["oura"].get("needs_clear")


def test_https_redirects_are_denied_and_errors_never_include_credentials(monkeypatch):
    handler = service._NoRedirect()
    assert handler.redirect_request(None, None, 302, "", {}, "https://elsewhere") is None
    for url in (
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com?token=secret",
    ):
        with pytest.raises(ServiceError):
            ServiceConfig(url).validated_url()

    class Failing:
        def open(self, *args, **kwargs):
            raise HTTPError("https://secret", 401, "token=private", {}, None)

    monkeypatch.setattr(service, "build_opener", lambda *a: Failing())
    with pytest.raises(ServiceError) as error:
        service.request_json("https://example.com", token="private")
    assert "private" not in str(error.value) and error.value.status == 401


def test_disabled_operator_switch_blocks_direct_reads_with_cached_credentials(
    connection, monkeypatch
):
    source, saved = connection
    monkeypatch.setattr(oura_api, "call_service", lambda *a, **k: {"oura_enabled": False})
    monkeypatch.setattr(oura_api, "request_json", lambda *a, **k: pytest.fail("Oura contacted"))
    with pytest.raises(ServiceError, match="not been enabled"):
        oura_api.sync_oura(source, CONFIG, SharingConfig())
    assert saved["oura"]["retry_at"] > time.time() + 3500
    assert not health.health_database_path(source).exists()


def test_denied_workout_scope_preserves_sleep_steps_without_reusing_workout_coverage(
    connection, monkeypatch
):
    from test_workouts import workout

    source, saved = connection
    health.save_oura_import(
        source,
        parse_oura_records([], [], [workout()]),
        authoritative_window=("2025-01-01", "2027-01-01"),
    )

    def request(url, **kwargs):
        if "/workout?" in url:
            raise ServiceError("Forbidden", status=403)
        return {
            "data": [sleep()] if "/sleep?" in url else [{"day": "2026-09-07", "steps": 4000}],
            "next_token": None,
        }

    monkeypatch.setattr(oura_api, "request_json", request)
    result = oura_api.sync_oura(source, CONFIG, SharingConfig())
    assert not result.snapshot.workouts and not result.snapshot.workout_windows
    assert result.snapshot.days[date(2026, 9, 7)].values["oura_steps"] == 4000
    assert result.snapshot.days[date(2026, 9, 7)].values["oura_workout_count"] is None
    assert saved["oura"]["workout_access"] is False
    assert "Workout access is unavailable" in oura_api.connection_summary(source)


def test_failed_or_invalid_workout_page_does_not_commit_sleep_deletions(connection, monkeypatch):
    from test_workouts import workout

    source, saved = connection
    before = health.save_oura_import(
        source,
        parse_oura_records([sleep()], [], [workout()]),
        authoritative_window=("2025-01-01", "2027-01-01"),
    ).snapshot

    def request(url, **kwargs):
        if "/workout?" in url:
            raise ServiceError("Rate limited", status=429, retry_after=1800)
        return {"data": [], "next_token": None}

    monkeypatch.setattr(oura_api, "request_json", request)
    with pytest.raises(ServiceError):
        oura_api.sync_oura(source, CONFIG, SharingConfig(), force=True)
    assert health.load_health(source) == before
    assert saved["oura"]["retry_at"] > time.time() + 1700
    monkeypatch.setattr(
        oura_api,
        "request_json",
        lambda url, **kwargs: {
            "data": [workout(start_datetime="invalid")] if "/workout?" in url else [],
            "next_token": None,
        },
    )
    with pytest.raises(ServiceError, match="invalid measurements"):
        oura_api.sync_oura(source, CONFIG, SharingConfig(), force=True)
    assert health.load_health(source) == before


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows DPAPI")
def test_real_os_vault_roundtrip_is_encrypted_and_profile_isolated(tmp_path):
    source = tmp_path / "first" / "collection.anki2"
    other = tmp_path / "second" / "collection.anki2"
    vault.write_secret(source, "oura", {"refresh_token": "synthetic-secret-only"})
    assert vault.read_secret(source, "oura") == {"refresh_token": "synthetic-secret-only"}
    assert vault.read_secret(other, "oura") is None
    assert (
        b"synthetic-secret-only"
        not in (storage.results_directory(source) / "connections.dpapi").read_bytes()
    )
    vault.write_secret(source, "oura", None)
    assert vault.read_secret(source, "oura") is None
