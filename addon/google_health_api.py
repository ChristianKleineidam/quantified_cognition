"""Google Health v4: hosted OAuth, direct reads, validated daily-only cache."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import webbrowser
from datetime import date, timedelta
from urllib.parse import urlencode, urlsplit

from .google_health import GoogleHealthImport, parse_google_health
from .health import load_health, save_google_health_import
from .oura_api import Loopback
from .service import ServiceConfig, ServiceError, call_service, request_json
from .storage import profile_job_lock
from .vault import read_secret, write_secret

SLOT = "google_health"
SLEEP_SCOPE = "https://www.googleapis.com/auth/googlehealth.sleep.readonly"
STEPS_SCOPE = "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
SCOPES = frozenset((SLEEP_SCOPE, STEPS_SCOPE))
ROOT = "https://health.googleapis.com/v4/users/me/dataTypes/"
FAMILY = "users/me/dataSourceFamilies/all-sources"
SLEEP_FIELDS = (
    "dataPoints(dataPointName,sleep(interval(startTime,endTime,endUtcOffset,civilEndTime),"
    "type,summary(minutesAsleep,minutesAwake,stagesSummary(type,minutes)),metadata(processed,mainSleep,nap))),"
    "nextPageToken"
)


def default_history_start():
    return (date.today() - timedelta(days=365)).isoformat()


def _tokens(value, config, previous=None):
    previous = previous or {}
    if not isinstance(value, dict):
        raise ServiceError("The Google connection service returned invalid credentials.")
    access = value.get("access_token")
    refresh = value.get("refresh_token") or previous.get("refresh_token")
    expires = value.get("expires_in")
    scope = value.get("scope")
    granted = scope.split() if isinstance(scope, str) else previous.get("scopes", [])
    if (
        not isinstance(access, str)
        or not access
        or not isinstance(refresh, str)
        or not refresh
        or isinstance(expires, bool)
        or not isinstance(expires, (int, float))
        or not 0 < expires < 366 * 86400
        or not granted
        or not set(granted) <= SCOPES
    ):
        raise ServiceError("Grant sleep or activity read access, then connect Google Health again.")
    return dict(
        access_token=access,
        refresh_token=refresh,
        expires_at=time.time() + expires,
        scopes=sorted(set(granted)),
        service=config.validated_url(),
    )


def _clear(source, sharing):
    from .insights import delete_cached_insights

    result = save_google_health_import(
        source,
        GoogleHealthImport({}),
        start="0001-01-01",
        end="9999-12-31",
        sharing=sharing,
        clear=True,
    )
    delete_cached_insights(source, "google_health")
    return result


def connect_google_health(
    source, config, sharing, *, history_start=None, check_cancelled=lambda: None, open_browser=None
):
    start = _history(history_start or default_history_start())
    config.validated_url()
    with profile_job_lock(source, SLOT):
        write_secret(source, "probe", None)
        callback = Loopback()
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        try:
            started = call_service(
                config,
                "/google/oauth/start",
                dict(
                    return_uri=callback.return_uri, state=callback.state, code_challenge=challenge
                ),
            )
            url = started.get("authorize_url", "")
            if (
                urlsplit(url)._replace(query="", fragment="").geturl()
                != "https://accounts.google.com/o/oauth2/v2/auth"
            ):
                raise ServiceError("The service returned an invalid Google sign-in address.")
            if not (open_browser or webbrowser.open)(url):
                raise ServiceError("The browser could not open Google sign-in.")
            code = callback.wait(check_cancelled, provider="Google Health")
            check_cancelled()
            value = call_service(
                config,
                "/google/oauth/exchange",
                dict(code=code, code_verifier=verifier, flow=started["flow"]),
            )
            credentials = _tokens(value, config)
            credentials.update(history_start=start, connected_at=time.time(), needs_clear=True)
            write_secret(source, SLOT, credentials)
            _clear(source, sharing)  # New account cannot inherit the old account's days.
            credentials.pop("needs_clear")
            write_secret(source, SLOT, credentials)
        finally:
            callback.close()
    return sync_google_health(source, config, sharing, force=True, check_cancelled=check_cancelled)


def _history(value):
    try:
        day = date.fromisoformat(value)
        if not date(1900, 1, 1) <= day <= date.today():
            raise ValueError()
        return day.isoformat()
    except (TypeError, ValueError):
        raise ValueError(
            "Choose a Google Health import start date between 1900 and today."
        ) from None


def _access(source, config, credentials, *, refresh=False):
    if credentials.get("service") != config.validated_url():
        raise ServiceError("The connection service changed. Connect Google Health again.")
    if refresh or credentials.get("expires_at", 0) < time.time() + 120:
        value = call_service(
            config, "/google/oauth/refresh", {"refresh_token": credentials["refresh_token"]}
        )
        credentials.update(_tokens(value, config, credentials))
        # Persist before cancellation or any data request; refresh may rotate.
        write_secret(source, SLOT, credentials)
    return credentials["access_token"]


def _civil(day):
    return {"date": {"year": day.year, "month": day.month, "day": day.day}}


def _pages(source, config, credentials, kind, start, end, check_cancelled):
    records, seen, cursor = [], set(), None
    while True:
        check_cancelled()
        if kind == "sleep":
            # Google documents filtering by interval START, not end. Pad the
            # physical query for overnight sleep/travel, then select wake dates
            # locally after validation. Overlapping queries are deduplicated.
            lower = (start - timedelta(days=9)).isoformat() + "T00:00:00Z"
            upper = (end + timedelta(days=1)).isoformat() + "T00:00:00Z"
            params = {
                "pageSize": 25,
                "dataSourceFamily": FAMILY,
                "fields": SLEEP_FIELDS,
                "filter": (
                    f'sleep.interval.start_time >= "{lower}" AND '
                    f'sleep.interval.start_time < "{upper}"'
                ),
            }
            if cursor:
                params["pageToken"] = cursor
            url, body, key = (
                ROOT + "sleep/dataPoints:reconcile?" + urlencode(params),
                None,
                "dataPoints",
            )
        else:
            body = {
                "range": {"start": _civil(start), "end": _civil(end)},
                "windowSizeDays": 1,
                "pageSize": 10000,
                "dataSourceFamily": FAMILY,
            }
            if cursor:
                body["pageToken"] = cursor
            url, key = ROOT + "steps/dataPoints:dailyRollUp", "rollupDataPoints"
        try:
            page = request_json(url, body, token=_access(source, config, credentials))
        except ServiceError as error:
            if error.status != 401:
                raise
            page = request_json(url, body, token=_access(source, config, credentials, refresh=True))
        rows = page.get(key, [])  # Empty repeated fields can be omitted by protobuf JSON.
        if not isinstance(rows, list):
            raise ServiceError("Google Health returned an invalid page. Saved data is unchanged.")
        records.extend(rows)
        if len(records) > 200000 or len(seen) > 10000:
            raise ServiceError("Google Health exceeded the import limit.")
        cursor = page.get("nextPageToken")
        if cursor is None or cursor == "":
            return records
        if not isinstance(cursor, str) or len(cursor) > 8192 or cursor in seen:
            raise ServiceError("Google Health returned an invalid pagination cursor.")
        seen.add(cursor)


def sync_google_health(
    source, config, sharing, *, force=False, history_start=None, check_cancelled=lambda: None
):
    requested = _history(history_start) if history_start else None
    with profile_job_lock(source, SLOT):
        credentials = read_secret(source, SLOT)
        now = time.time()
        if not credentials or (not force and now < credentials.get("retry_at", 0)):
            return None
        if credentials.get("reauth_required"):
            if force:
                raise ServiceError(
                    "Google authorization expired. Select Connect Google Health again."
                )
            return None
        backfill = (
            SLEEP_SCOPE in credentials["scopes"] and credentials.get("sleep_session_version") != 1
        )
        if (
            not force
            and not requested
            and not backfill
            and now - credentials.get("last_sync", 0) < 3600
        ):
            return None
        if credentials.get("needs_clear"):
            _clear(source, sharing)
            credentials.pop("needs_clear")
            write_secret(source, SLOT, credentials)
        earliest = _history(credentials.get("history_start", default_history_start()))
        if requested:
            earliest = min(earliest, requested)
        full = backfill or bool(requested) or now - credentials.get("last_full_sync", 0) > 7 * 86400
        start = (
            date.fromisoformat(earliest)
            if full
            else max(date.fromisoformat(earliest), date.today() - timedelta(days=30))
        )
        end = date.today() + timedelta(days=2)
        nights, steps = [], []
        try:
            check_cancelled()
            status = call_service(config, "/status")
            if status.get("google_health_enabled") is not True:
                raise ServiceError(
                    "Google Health access has not been enabled by the operator yet.",
                    status=503,
                    retry_after=3600,
                )
            # Refresh once first so permission changes are reflected in the reads.
            _access(source, config, credentials)
            cursor = start
            while cursor < end:
                stop = min(end, cursor + timedelta(days=90))
                if SLEEP_SCOPE in credentials["scopes"]:
                    nights.extend(
                        _pages(source, config, credentials, "sleep", cursor, stop, check_cancelled)
                    )
                if STEPS_SCOPE in credentials["scopes"]:
                    steps.extend(
                        _pages(source, config, credentials, "steps", cursor, stop, check_cancelled)
                    )
                if len(nights) + len(steps) > 200000:
                    raise ServiceError("Google Health exceeded the import limit.")
                cursor = stop
            prepared = parse_google_health(
                nights,
                steps,
                start=start.isoformat(),
                end=end.isoformat(),
                filter_sleep_window=True,
                sleep_fetched=SLEEP_SCOPE in credentials["scopes"],
                steps_fetched=STEPS_SCOPE in credentials["scopes"],
            )
            check_cancelled()
            result = save_google_health_import(
                source,
                prepared,
                start=start.isoformat(),
                end=end.isoformat(),
                sharing=sharing,
                check_cancelled=check_cancelled,
            )
            credentials.update(last_sync=now, last_error="", retry_at=0, history_start=earliest)
            if full:
                credentials["last_full_sync"] = now
                if SLEEP_SCOPE in credentials["scopes"]:
                    credentials["sleep_session_version"] = 1
            write_secret(source, SLOT, credentials)
            return result
        except (ServiceError, ValueError) as error:
            credentials.update(
                last_error=str(error), retry_at=now + getattr(error, "retry_after", 300)
            )
            if getattr(error, "status", 0) == 401:
                credentials["reauth_required"] = True
            write_secret(source, SLOT, credentials)
            raise


def disconnect_google_health(source, config, sharing):
    with profile_job_lock(source, SLOT):
        credentials = read_secret(source, SLOT)
        write_secret(source, SLOT, None)
        result = _clear(source, sharing)
        message = "Disconnected. Local Google Health data was deleted and future syncing stopped."
        if result.csv_error:
            message += " Fixed CSV update is pending: " + result.csv_error
        if credentials:
            try:
                call_service(
                    ServiceConfig(credentials["service"]),
                    "/google/oauth/revoke",
                    {"token": credentials["refresh_token"]},
                )
            except ServiceError:
                message += (
                    " Also remove this app’s access in your Google account; "
                    "revocation could not be confirmed."
                )
        return result.snapshot, message


def saved_google_health(source):
    credentials = read_secret(source, SLOT)
    if not credentials:
        message = "Google Health is not connected."
    elif credentials.get("last_error"):
        message = credentials["last_error"]
    else:
        available = ", ".join(
            name
            for scope, name in ((SLEEP_SCOPE, "sleep"), (STEPS_SCOPE, "steps"))
            if scope in credentials.get("scopes", [])
        )
        message = (
            f"Google Health is connected for {available}. "
            f"Import since {credentials['history_start']}; background sync is on."
        )
    return load_health(source), message
