"""Oura OAuth and direct API sync, entirely independent of Anki's collection."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from .health import save_oura_import
from .oura import OuraImport, parse_oura_records
from .service import ServiceError, call_service, request_json
from .storage import profile_job_lock
from .vault import read_secret, write_secret

SLEEP_PROJECTION = (
    "id,day,type,bedtime_start,bedtime_end,total_sleep_duration,light_sleep_duration,"
    "deep_sleep_duration,rem_sleep_duration,awake_time,sleep_algorithm_version"
)
WORKOUT_PROJECTION = "id,day,start_datetime,end_datetime,intensity,source"


def _clear_local(source, sharing):
    from .insights import delete_cached_insights

    result = save_oura_import(source, OuraImport((), ()), sharing=sharing, clear=True)
    delete_cached_insights(source, "oura")
    return result


def _tokens(value, config):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("access_token"), str)
        or not value["access_token"]
        or not isinstance(value.get("refresh_token"), str)
        or not value["refresh_token"]
        or not isinstance(value.get("expires_in"), (int, float))
        or not 0 < value["expires_in"] < 366 * 86400
    ):
        raise ServiceError("The connection service returned invalid credentials.")
    return {
        "access_token": value["access_token"],
        "refresh_token": value["refresh_token"],
        "expires_at": time.time() + value["expires_in"],
        "service": config.validated_url(),
        "authorization_proof": value.get("authorization_proof", ""),
    }


class Loopback:
    """Single-use random path + state + PKCE; authorization codes never logged."""

    def __init__(self):
        self.state = secrets.token_urlsafe(32)
        self.path = "/" + secrets.token_urlsafe(32)
        self.result = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                uri = urlsplit(self.path)
                query = parse_qs(uri.query)
                valid = (
                    uri.path == owner.path
                    and query.get("state") == [owner.state]
                    and owner.result is None
                )
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                if valid:
                    owner.result = query
                self.wfile.write(
                    b"You can return to Anki." if valid else b"Invalid sign-in response."
                )

            def log_message(self, *_args):
                pass

        class Server(HTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(2)
                return connection, address

        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.timeout = 0.25
        self.return_uri = f"http://127.0.0.1:{self.server.server_port}{self.path}"

    def wait(self, check_cancelled, timeout=180, provider="Oura"):
        deadline = time.monotonic() + timeout
        while self.result is None:
            check_cancelled()
            if time.monotonic() >= deadline:
                raise ServiceError(f"Sign-in timed out. Try connecting to {provider} again.")
            self.server.handle_request()
        if self.result.get("error") or len(self.result.get("code", [])) != 1:
            raise ServiceError(f"{provider} sign-in was cancelled or denied.")
        return self.result["code"][0]

    def close(self):
        self.server.server_close()


def connect_oura(source, config, sharing, check_cancelled=lambda: None, open_browser=None):
    config.validated_url()
    with profile_job_lock(source, "oura"):
        # Verify the OS vault is usable before the user spends time signing in.
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
                "/oauth/start",
                {
                    "return_uri": callback.return_uri,
                    "state": callback.state,
                    "code_challenge": challenge,
                },
            )
            url = started.get("authorize_url", "")
            if (
                urlsplit(url)._replace(query="", fragment="").geturl()
                != "https://cloud.ouraring.com/oauth/authorize"
            ):
                raise ServiceError("The connection service returned an invalid sign-in address.")
            if not (open_browser or webbrowser.open)(url):
                raise ServiceError("The browser could not open the Oura sign-in page.")
            code = callback.wait(check_cancelled)
            check_cancelled()
            value = call_service(
                config,
                "/oauth/exchange",
                {"code": code, "code_verifier": verifier, "flow": started["flow"]},
            )
            credentials = _tokens(value, config)
            # Rotated credentials must be durable even if Qt/profile closes now.
            credentials["connected_at"] = time.time()
            credentials["needs_clear"] = True
            write_secret(source, "oura", credentials)
            # Never mix the previous account's local history into the new account.
            _clear_local(source, sharing)
            credentials.pop("needs_clear")
            write_secret(source, "oura", credentials)
        finally:
            callback.close()
    return sync_oura(source, config, sharing, force=True, check_cancelled=check_cancelled)


def _access(source, config, credentials, *, refresh=False):
    if credentials.get("service") != config.validated_url():
        raise ServiceError("The connection service changed. Select Change Oura permissions.")
    if refresh or credentials.get("expires_at", 0) < time.time() + 120:
        value = call_service(
            config, "/oauth/refresh", {"refresh_token": credentials["refresh_token"]}
        )
        credentials.update(_tokens(value, config))
        write_secret(source, "oura", credentials)  # Single-use refresh token rotates.
    return credentials["access_token"]


def _collection(source, config, credentials, kind, start, end, check_cancelled):
    records, seen, cursor = [], set(), None
    while True:
        check_cancelled()
        params = {
            "start_date": start,
            "end_date": end,
            "fields": {
                "sleep": SLEEP_PROJECTION,
                "workout": WORKOUT_PROJECTION,
                "daily_activity": "id,day,steps",
            }[kind],
        }
        if cursor:
            params["next_token"] = cursor
        url = "https://api.ouraring.com/v2/usercollection/" + kind + "?" + urlencode(params)
        for refresh in (False, True):
            token = _access(source, config, credentials, refresh=refresh)
            try:
                page = request_json(url, token=token)
            except ServiceError as error:
                if error.status == 403 and cursor is None:
                    return None  # This dataset is unavailable to the current grant.
                if error.status != 401 or refresh:
                    raise
            else:
                break
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise ServiceError("Oura returned an invalid data page. Saved data is unchanged.")
        records.extend(page["data"])
        if len(records) > 200000 or len(seen) > 10000:
            raise ServiceError("The Oura response exceeded the record limit.")
        cursor = page.get("next_token")
        if cursor is None:
            return records
        if not isinstance(cursor, str) or not cursor or len(cursor) > 8192 or cursor in seen:
            raise ServiceError("Oura returned an invalid pagination cursor.")
        seen.add(cursor)


def sync_oura(source, config, sharing, *, force=False, check_cancelled=lambda: None):
    # This lock serializes single-use refresh tokens across overlapping processes.
    # It never blocks Anki operations, CSV updates, or the collection worker queue.
    with profile_job_lock(source, "oura"):
        credentials = read_secret(source, "oura")
        now = time.time()
        if not credentials or (not force and now < credentials.get("retry_at", 0)):
            return None
        if credentials.get("reauth_required"):
            if force:
                raise ServiceError("Oura authorization expired. Select Change Oura permissions.")
            return None
        backfill = (
            credentials.get("sleep_session_version") != 1 or credentials.get("workout_version") != 1
        )
        if not force and not backfill and now - credentials.get("last_sync", 0) < 3600:
            return None
        if credentials.get("needs_clear"):
            _clear_local(source, sharing)
            credentials.pop("needs_clear")
            write_secret(source, "oura", credentials)
        today = date.today()
        full = backfill or now - credentials.get("last_full_sync", 0) > 7 * 86400
        start = "2000-01-01" if full else (today - timedelta(days=30)).isoformat()
        end = (today + timedelta(days=2)).isoformat()  # Includes provider days east of local time.
        try:
            if not call_service(config, "/status").get("oura_enabled"):
                raise ServiceError(
                    "Oura access has not been enabled by the operator yet.", retry_after=3600
                )
            nights = _collection(source, config, credentials, "sleep", start, end, check_cancelled)
            activity = (
                _collection(
                    source, config, credentials, "daily_activity", start, end, check_cancelled
                )
                if nights is not None
                else None
            )
            workouts = _collection(
                source, config, credentials, "workout", start, end, check_cancelled
            )
            daily_access = activity is not None  # Sleep and steps share the Daily permission.
            workout_access = workouts is not None
            if not (daily_access or workout_access):
                raise ServiceError(
                    "Oura could not provide sleep, steps or workouts. Check your membership "
                    "or reconnect and allow Daily or Workout.",
                    status=403,
                )
            try:
                prepared = parse_oura_records(
                    nights if daily_access else [], activity or [], workouts
                )
            except ValueError:
                raise ServiceError(
                    "Oura returned invalid measurements. Saved data is unchanged.", retry_after=3600
                ) from None
            # Oura may include an inclusive end boundary. Commit only our window.
            prepared = OuraImport(
                tuple(r for r in prepared.sleep if start <= r.day < end),
                tuple(r for r in prepared.activity if start <= r.day < end),
                workouts=tuple(r for r in prepared.workouts if start <= r.day < end),
                workouts_fetched=prepared.workouts_fetched,
            )
            check_cancelled()
            result = save_oura_import(
                source,
                prepared,
                sharing=sharing,
                authoritative_window=(start, end),
                daily_unavailable=not daily_access,
                workouts_unavailable=not workout_access,
                check_cancelled=check_cancelled,
            )
            if not full and (
                (daily_access and credentials.get("daily_access") is False)
                or (workout_access and credentials.get("workout_access") is False)
            ):
                # Restored access needs a full history on the next scheduled sync.
                credentials["last_full_sync"] = 0
            credentials.update(
                last_sync=now,
                last_error="",
                retry_at=0,
                daily_access=daily_access,
                workout_access=workout_access,
            )
            if full:
                credentials["last_full_sync"] = now
                credentials["sleep_session_version"] = 1
                credentials["workout_version"] = 1
            write_secret(source, "oura", credentials)
            from .oura_research import renew_authorization

            try:
                renew_authorization(source, config, credentials=credentials)
            except ServiceError:
                # Personal sync stays usable; server research leases expire closed.
                pass
            return result
        except ServiceError as error:
            credentials.update(last_error=str(error), retry_at=now + error.retry_after)
            if error.status == 401:
                credentials["reauth_required"] = True
            write_secret(source, "oura", credentials)
            raise


def disconnect_oura(source, config, sharing):
    from .oura_research import withdraw_on_disconnect

    research_message = withdraw_on_disconnect(source)
    with profile_job_lock(source, "oura"):
        credentials = read_secret(source, "oura")
        write_secret(source, "oura", None)
        result = _clear_local(source, sharing)
        message = "Disconnected. Local Oura data was deleted and future syncing stopped."
        if result.csv_error:
            message += " Fixed CSV update is pending: " + result.csv_error
        if credentials:
            try:
                call_service(config, "/oauth/revoke", {"access_token": credentials["access_token"]})
            except ServiceError:
                message += (
                    " Also revoke this app in your Oura account; the service could not be reached."
                )
        return result.snapshot, message + research_message


def connection_status(source):
    credentials = read_secret(source, "oura")
    if not credentials:
        return "Oura is not connected.", False
    if credentials.get("last_error"):
        return credentials["last_error"], True
    if credentials.get("workout_access") is False:
        return (
            "Sleep and steps are connected. Workout access is unavailable. "
            "Select Change Oura permissions and allow Workout.",
            True,
        )
    if credentials.get("daily_access") is False:
        return (
            "Workouts are connected. Sleep and step access is unavailable. "
            "Select Change Oura permissions and allow Daily.",
            True,
        )
    return (
        "Oura is connected. Sleep, steps and workouts sync automatically while Anki is open.",
        True,
    )
