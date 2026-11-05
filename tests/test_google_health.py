"""Synthetic provider fixtures: never actual account or health data."""

import copy
import csv
import time
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from test_oura import sleep as oura_sleep
from test_storage import report_with_followup

from addon import google_health_api as api
from addon import health, storage
from addon.backend import UpdateCancelled
from addon.google_health import GoogleHealthImport, parse_google_health
from addon.oura import parse_oura_records
from addon.research import SharingConfig, sharing_state
from addon.service import ServiceConfig, ServiceError

CONFIG = ServiceConfig("https://example.supabase.co/functions/v1/qs-service")
DAY = date(2026, 9, 7)


def civil(day):
    return {"date": dict(year=day.year, month=day.month, day=day.day)}


def night(kind="STAGES", **changes):
    result = {
        "dataPointName": "users/synthetic-private-user/dataTypes/sleep/dataPoints/private-id",
        "sleep": {
            "interval": {
                "startTime": "2026-09-06T21:00:00Z",
                "endTime": "2026-09-07T05:00:00Z",
                "endUtcOffset": "7200s",
            },
            "type": kind,
            "metadata": {"processed": True, "mainSleep": True},
            "summary": {
                "minutesAsleep": "420",
                "minutesAwake": "60",
                "stagesSummary": [
                    {"type": name, "minutes": str(minutes)}
                    for name, minutes in (("LIGHT", 210), ("DEEP", 90), ("REM", 120), ("AWAKE", 60))
                ],
            },
        },
    }
    result["sleep"].update(changes)
    return result


def steps(day=DAY, count="8300"):
    return {
        "civilStartTime": civil(day),
        "civilEndTime": civil(day + timedelta(days=1)),
        "steps": {"countSum": count},
    }


def parsed(nights=None, counts=None):
    return parse_google_health(nights or [], counts or [], start="2026-09-01", end="2026-10-01")


def test_summaries_use_provider_minutes_without_double_counting_short_awakenings():
    raw = night()
    raw["sleep"]["shortAwakenings"] = [{"minutes": "10"}]
    raw["sleep"]["stages"] = [{"minutes": "480"}]
    values = parsed([raw, copy.deepcopy(raw)], [steps(count="0")]).days[DAY]
    assert values["google_health_total_sleep_seconds"] == 25200
    assert values["google_health_light_sleep_seconds"] == 12600
    assert values["google_health_awake_seconds"] == 3600
    assert values["google_health_sleep_period_count"] == 1
    assert values["google_health_steps"] == 0


def test_classic_sleep_restless_is_not_fabricated_as_deep_or_light():
    summary = {
        "minutesAsleep": "400",
        "minutesAwake": "60",
        "stagesSummary": [
            {"type": "ASLEEP", "minutes": "400"},
            {"type": "RESTLESS", "minutes": "20"},
        ],
    }
    values = parsed([night("CLASSIC", summary=summary)]).days[DAY]
    assert values["google_health_total_sleep_seconds"] == 24000
    assert values["google_health_restless_seconds"] == 1200
    assert values["google_health_light_sleep_seconds"] is None
    assert values["google_health_sleep_status"] == "stages unavailable"


def test_missing_observations_are_distinct_from_zero_and_both_full_day_boundaries_work():
    missing = steps(count="0")
    missing.pop("steps")
    row = steps(DAY + timedelta(days=1))
    row["civilEndTime"] = {
        **civil(DAY + timedelta(days=1)),
        "time": {"hours": 23, "minutes": 59, "seconds": 59},
    }
    values = parsed(counts=[missing, row]).days
    assert values[DAY]["google_health_steps"] is None
    assert values[DAY + timedelta(days=1)]["google_health_steps"] == 8300
    row["civilEndTime"]["time"]["hours"] = 22
    with pytest.raises(ValueError):
        parsed(counts=[row])


@pytest.mark.parametrize("value", [True, -1, "NaN", "Infinity", "1.5", "9007199254740993"])
def test_invalid_counts_rejected(value):
    with pytest.raises(ValueError):
        parsed(counts=[steps(count=value)])


def test_overlapping_periods_and_processing_blank_totals():
    other = night()
    other["dataPointName"] = "another"
    other["sleep"]["interval"]["startTime"] = "2026-09-06T22:00:00Z"
    other["sleep"]["interval"]["endTime"] = "2026-09-07T06:00:00Z"
    values = parsed([night(), other]).days[DAY]
    assert values["google_health_sleep_status"] == "overlapping periods"
    assert values["google_health_total_sleep_seconds"] is None
    values = parsed([night(metadata={"processed": False})]).days[DAY]
    assert values["google_health_sleep_status"] == "processing"
    assert values["google_health_total_sleep_seconds"] is None


def test_wake_date_uses_session_offset_across_dst_and_utc_midnight():
    raw = night()
    raw["sleep"]["interval"] = {
        "startTime": "2026-09-06T15:00:00Z",
        "endTime": "2026-09-06T23:00:00Z",
        "endUtcOffset": "7200s",
        "civilEndTime": {**civil(DAY), "time": {"hours": 1}},
    }
    assert DAY in parsed([raw]).days
    raw["sleep"]["interval"]["endUtcOffset"] = "0s"
    with pytest.raises(ValueError, match="inconsistent"):
        parsed([raw])
    raw = night()
    raw["sleep"]["interval"] = {
        "startTime": "2026-10-24T21:00:00Z",
        "endTime": "2026-10-25T05:00:00Z",
        "endUtcOffset": "3600s",
    }
    result = parse_google_health([raw], [], start="2026-10-25", end="2026-10-26")
    assert result.days[date(2026, 10, 25)]["google_health_total_sleep_seconds"] == 25200


def test_migration_preserves_oura_and_daily_only_google_revision_and_deletion(tmp_path):
    source = tmp_path / "collection.anki2"
    health.save_oura_import(source, parse_oura_records([oura_sleep()], []))
    path = health.health_database_path(source)
    from addon.private_storage import private_database

    with private_database(path, write=True) as con:
        con.execute("drop table google_health_days")
        con.execute("pragma user_version=1")
    assert health.load_health(source).days[DAY].values["oura_total_sleep_seconds"] == 25200
    first = health.save_google_health_import(
        source, parsed([night()], [steps()]), start="2026-09-01", end="2026-10-01"
    )
    again = health.save_google_health_import(
        source, parsed([night()], [steps()]), start="2026-09-01", end="2026-10-01"
    )
    assert again.changed_records == 0 and again.snapshot == first.snapshot
    assert first.snapshot.days[DAY].values["oura_total_sleep_seconds"] == 25200
    assert b"synthetic-private-user" not in path.read_bytes()
    sessions = [s for s in first.snapshot.sessions if s.provider == "google_health"]
    assert len(sessions) == 1 and sessions[0].kind == "main"
    assert sessions[0].end - sessions[0].start == 8 * 3600
    cleared = health.save_google_health_import(
        source, GoogleHealthImport({}), start="2026-09-01", end="2026-10-01"
    )
    assert cleared.changed_records == 2
    assert cleared.snapshot.days[DAY].values["google_health_total_sleep_seconds"] is None
    assert cleared.snapshot.days[DAY].values["oura_data_version"] == 1


def test_changed_health_refreshes_combined_csv_without_fsrs_and_csv_retry_is_separate(
    tmp_path, monkeypatch
):
    source, destination = tmp_path / "collection.anki2", tmp_path / "fixed.csv"
    storage.save_report(report_with_followup(), storage.database_path(source))
    storage.save_daily_settings(
        source, storage.DailySettings(enabled=True, csv_path=str(destination))
    )
    monkeypatch.setattr(storage, "build_report", lambda *a, **k: pytest.fail("FSRS replay"))
    original = storage.retry_fixed_csv
    monkeypatch.setattr(
        storage, "retry_fixed_csv", lambda *a, **k: (_ for _ in ()).throw(OSError("File locked"))
    )
    result = health.save_google_health_import(
        source, parsed([night()], [steps(count="0")]), start="2026-09-01", end="2026-10-01"
    )
    assert result.changed_records == 2 and "File locked" in result.csv_error
    monkeypatch.setattr(storage, "retry_fixed_csv", original)
    assert original(source).csv_exported
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        rows = {row["date"]: row for row in csv.DictReader(stream)}
    assert rows[DAY.isoformat()]["google_health_steps"] == "0"
    assert "recall_score_pp" in rows[DAY.isoformat()]


@pytest.fixture
def connection(tmp_path, monkeypatch):
    source = tmp_path / "collection.anki2"
    secrets = {
        api.SLOT: dict(
            access_token="access",
            refresh_token="refresh",
            expires_at=time.time() + 7200,
            service=CONFIG.base_url,
            scopes=sorted(api.SCOPES),
            history_start="2026-09-01",
        )
    }
    monkeypatch.setattr(api, "read_secret", lambda path, slot: copy.deepcopy(secrets.get(slot)))
    monkeypatch.setattr(
        api, "write_secret", lambda path, slot, value: secrets.update({slot: copy.deepcopy(value)})
    )

    def status(config, route, data=None):
        assert route == "/status"
        return {"google_health_enabled": True}

    monkeypatch.setattr(api, "call_service", status)
    return source, secrets


def test_google_paginated_sync_is_atomic_direct_minimal_and_throttled(connection, monkeypatch):
    source, secrets = connection
    secrets[api.SLOT].update(last_sync=time.time(), last_full_sync=time.time())
    calls = []

    def request(url, data, *, token):
        calls.append((url, data))
        assert token == "access" and url.startswith("https://health.googleapis.com/")
        if "sleep/" in url:
            query = parse_qs(urlsplit(url).query)
            assert "start_time" in query["filter"][0] and "end_time" not in query["filter"][0]
            assert (
                "stagesSummary" in query["fields"][0]
                and "shortAwakenings" not in query["fields"][0]
            )
            return {
                "dataPoints": [] if "pageToken" in query else [night()],
                "nextPageToken": "" if "pageToken" in query else "next",
            }
        assert data["windowSizeDays"] == 1 and data["dataSourceFamily"] == api.FAMILY
        lower, upper = (date(**data["range"][key]["date"]) for key in ("start", "end"))
        return {"rollupDataPoints": [steps()] if lower <= DAY < upper else []}

    monkeypatch.setattr(api, "request_json", request)
    result = api.sync_google_health(source, CONFIG, SharingConfig())
    assert len(calls) == 3 and result.snapshot.days[DAY].values["google_health_steps"] == 8300
    assert api.sync_google_health(source, CONFIG, SharingConfig()) is None
    assert api.sync_google_health(source, CONFIG, SharingConfig(), force=True).changed_records == 0
    assert secrets[api.SLOT]["last_full_sync"] > 0
    assert secrets[api.SLOT]["sleep_session_version"] == 1


def test_steps_only_permission_does_not_repeat_full_backfill(connection, monkeypatch):
    source, secrets = connection
    secrets[api.SLOT].update(
        scopes=[api.STEPS_SCOPE], last_sync=time.time(), last_full_sync=time.time()
    )
    monkeypatch.setattr(api, "request_json", lambda *a, **k: pytest.fail("Repeated steps sync"))
    assert api.sync_google_health(source, CONFIG, SharingConfig()) is None


def test_partial_scope_chunks_ninety_days_and_failure_preserves_cache(connection, monkeypatch):
    source, secrets = connection
    secrets[api.SLOT].update(scopes=[api.STEPS_SCOPE], history_start="2025-01-01")
    health.save_google_health_import(
        source, parsed([night()], [steps()]), start="2026-09-01", end="2026-10-01"
    )
    before = health.load_health(source)
    requests = []

    def request(url, body, **kwargs):
        assert "steps/" in url
        start, end = (date(**body["range"][key]["date"]) for key in ("start", "end"))
        assert 0 < (end - start).days <= 90
        requests.append((start, end))
        if len(requests) == 2:
            raise ServiceError("Rate limit", status=429, retry_after=1800)
        return {}

    monkeypatch.setattr(api, "request_json", request)
    with pytest.raises(ServiceError):
        api.sync_google_health(source, CONFIG, SharingConfig())
    assert health.load_health(source) == before and len(requests) == 2
    assert secrets[api.SLOT]["retry_at"] >= time.time() + 1700
    assert api.sync_google_health(source, CONFIG, SharingConfig()) is None


def test_refresh_without_replacement_retains_refresh_and_scopes_before_cancel(
    connection, monkeypatch
):
    source, secrets = connection
    secrets[api.SLOT]["expires_at"] = 0
    monkeypatch.setattr(
        api,
        "call_service",
        lambda cfg, route, data=None: (
            {"google_health_enabled": True}
            if route == "/status"
            else {"access_token": "renewed", "expires_in": 3600}
        ),
    )

    def request(*a, **k):
        assert secrets[api.SLOT]["refresh_token"] == "refresh"
        assert secrets[api.SLOT]["access_token"] == "renewed"
        raise UpdateCancelled("Closed")

    monkeypatch.setattr(api, "request_json", request)
    with pytest.raises(UpdateCancelled):
        api.sync_google_health(source, CONFIG, SharingConfig())
    assert not health.health_database_path(source).exists()


def test_revoked_tokens_stop_background_retry_and_cursor_cycles_preserve_data(
    connection, monkeypatch
):
    source, secrets = connection
    monkeypatch.setattr(
        api, "request_json", lambda *a, **k: {"dataPoints": [], "nextPageToken": "same"}
    )
    with pytest.raises(ServiceError, match="pagination"):
        api.sync_google_health(source, CONFIG, SharingConfig())
    assert not health.health_database_path(source).exists()
    secrets[api.SLOT]["expires_at"] = 0

    def revoked(config, route, data=None):
        if route == "/status":
            return {"google_health_enabled": True}
        raise ServiceError("Revoked", status=401)

    monkeypatch.setattr(api, "call_service", revoked)
    with pytest.raises(ServiceError):
        api.sync_google_health(source, CONFIG, SharingConfig(), force=True)
    assert secrets[api.SLOT]["reauth_required"]
    assert api.sync_google_health(source, CONFIG, SharingConfig()) is None


def test_expanded_notice_requires_new_choice_and_preserves_original_timing(tmp_path):
    source = tmp_path / "collection.anki2"
    old, new = SharingConfig("research-sharing-v2", "Old scope"), SharingConfig()
    sharing_state(source, old, event_type="sharing_choice", choice="opt_in")
    sharing_state(source, old, event_type="results_exposed", surface="report")
    assert sharing_state(source, new).current_choice is None
    current = sharing_state(source, new, event_type="sharing_choice", choice="opt_in")
    assert current.metadata()["research_consent_before_first_view"] is True
    assert current.metadata()["current_notice_consent_before_first_view"] is False


def test_connect_validates_browser_and_grants_and_keeps_oura_history(connection, monkeypatch):
    source, secrets = connection
    health.save_oura_import(source, parse_oura_records([oura_sleep()], []))
    health.save_google_health_import(
        source, parsed([night()]), start="2026-09-01", end="2026-10-01"
    )
    closed = []

    class Callback:
        state, return_uri = "x" * 43, "http://127.0.0.1:12345/" + "p" * 43

        def wait(self, check_cancelled, *, provider):
            assert provider == "Google Health"
            return "one-use"

        def close(self):
            closed.append(True)

    def service(config, route, data):
        if route.endswith("start"):
            assert len(data["code_challenge"]) == 43
            return {
                "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth?synthetic",
                "flow": "flow",
            }
        assert route.endswith("exchange") and data["code"] == "one-use"
        assert len(data["code_verifier"]) >= 43
        return {
            "access_token": "new-account",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "scope": api.SLEEP_SCOPE,
        }

    monkeypatch.setattr(api, "Loopback", Callback)
    monkeypatch.setattr(api, "call_service", service)
    monkeypatch.setattr(api, "sync_google_health", lambda *a, **k: "synced")
    assert (
        api.connect_google_health(source, CONFIG, SharingConfig(), open_browser=lambda url: True)
        == "synced"
    )
    assert closed == [True] and secrets[api.SLOT]["scopes"] == [api.SLEEP_SCOPE]
    values = health.load_health(source).days[DAY].values
    assert (
        values["oura_total_sleep_seconds"] == 25200
        and values["google_health_total_sleep_seconds"] is None
    )


def test_disconnect_clears_only_google_and_revokes_refresh_token(connection, monkeypatch):
    source, secrets = connection
    health.save_oura_import(source, parse_oura_records([oura_sleep()], []))
    health.save_google_health_import(
        source, parsed([night()]), start="2026-09-01", end="2026-10-01"
    )
    calls = []
    monkeypatch.setattr(api, "call_service", lambda cfg, route, data: calls.append((route, data)))
    snapshot, _ = api.disconnect_google_health(source, CONFIG, SharingConfig())
    assert secrets[api.SLOT] is None
    assert calls == [("/google/oauth/revoke", {"token": "refresh"})]
    assert snapshot.days[DAY].values["google_health_day_basis"] is None
    assert snapshot.days[DAY].values["oura_total_sleep_seconds"] == 25200


def test_operator_disable_prevents_google_reads_with_valid_cached_token(connection, monkeypatch):
    source, secrets = connection
    monkeypatch.setattr(api, "call_service", lambda *a, **k: {"google_health_enabled": False})
    monkeypatch.setattr(api, "request_json", lambda *a, **k: pytest.fail("Google was contacted"))
    with pytest.raises(ServiceError, match="not been enabled"):
        api.sync_google_health(source, CONFIG, SharingConfig())
    assert secrets[api.SLOT]["retry_at"] > time.time() + 3500
    assert not health.health_database_path(source).exists()
