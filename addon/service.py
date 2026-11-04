"""Small HTTPS transport; credentials never follow redirects or enter errors."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ServiceError(Exception):
    def __init__(self, message, *, status=0, retry_after=300):
        super().__init__(message)
        self.status = status
        self.retry_after = min(86400, max(60, retry_after))


@dataclass(frozen=True)
class ServiceConfig:
    base_url: str = ""

    @classmethod
    def from_config(cls, config):
        data = (config or {}).get("services", {})
        value = data.get("base_url", "") if isinstance(data, dict) else ""
        return cls(value.strip().rstrip("/") if isinstance(value, str) else "")

    def validated_url(self):
        parts = urlsplit(self.base_url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ServiceError(
                "The online service is not configured. Personal Anki data still works."
            )
        return self.base_url


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, data=None, *, token=None, method=None, limit=8 * 1024 * 1024):
    if urlsplit(url).scheme != "https":
        raise ServiceError("This service requires HTTPS.")
    headers = {"Accept": "application/json", "User-Agent": "QuantifiedCognition/0.19.4"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if data is not None:
        body = json.dumps(data, allow_nan=False, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ServiceError("The response exceeded the size limit.")
            value = json.loads(raw) if raw else {}
            if not isinstance(value, dict):
                raise ServiceError("The service returned an invalid response.")
            return value
    except HTTPError as error:
        try:
            retry = int(error.headers.get("Retry-After", "300"))
        except ValueError:
            try:
                retry = int(
                    (
                        parsedate_to_datetime(error.headers["Retry-After"])
                        - datetime.now(timezone.utc)
                    ).total_seconds()
                )
            except (ValueError, TypeError, KeyError):
                retry = 300
        messages = {
            401: "The connection expired or was revoked. Please connect again.",
            403: "Access was denied. Check the account membership and granted permissions.",
            409: "This submission ID was already used for different data.",
            410: "This research connection was deleted. Prepare a new submission.",
            429: "The service is busy. It will be retried later.",
            503: "This online feature has not been enabled by the operator yet.",
        }
        raise ServiceError(
            messages.get(error.code, "The online request failed. Please retry."),
            status=error.code,
            retry_after=retry,
        ) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ServiceError("Could not reach the service. Your saved data is unchanged.") from None


def call_service(config, route, data=None, **kwargs):
    return request_json(config.validated_url() + route, data, **kwargs)
