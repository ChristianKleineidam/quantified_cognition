"""Check the intended backend feature state without uploading measurements."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request(url, *, method="GET", headers=None, data=None, return_headers=False):
    if urlsplit(url).scheme != "https":
        raise ValueError("Verification requires HTTPS.")
    encoded = json.dumps(data).encode() if data is not None else None
    req = Request(
        url,
        data=encoded,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        response = build_opener(NoRedirect()).open(req, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read(1024 * 1024)
        result = response.code, json.loads(raw) if raw else {}
        return (*result, response.headers) if return_headers else result


def check(service_url, *, publishable_key=None, check_quota=False,
          expected_oura=False, expected_research=False):
    root = service_url.rstrip("/")
    status, payload = request(root + "/status")
    assert status == 200, f"Status endpoint returned {status}"
    assert payload.get("oura_enabled") is expected_oura, "Unexpected Oura state"
    assert payload.get("google_health_enabled") is False, "Google Health must remain disabled"
    assert payload.get("research_enabled") is expected_research, "Unexpected research state"
    assert payload.get("operations_version") == 1, "Backend quotas/monitoring version is missing"
    assert payload.get("research_controls_version") == 1, (
        "Enrollment/lifecycle controls are missing"
    )
    results = {"status": payload, "endpoint_checks": {}}
    # A random, nonexistent test identity; no registration or upload is allowed.
    auth = {"Authorization": "Bearer " + str(uuid.uuid4()) + ":" + "s" * 43}
    for route in (
        "/oauth/start",
        "/oauth/exchange",
        "/oauth/refresh",
        "/oauth/revoke",
        "/google/oauth/start",
        "/google/oauth/exchange",
        "/google/oauth/refresh",
        "/google/oauth/revoke",
        "/research/participant",
        "/research/submissions",
        "/research/enrollment",
    ):
        research = route.startswith("/research/")
        if route.startswith("/oauth/") and expected_oura:
            continue  # Do not exercise real provider credentials to check a flag.
        expected = 401 if research and expected_research else 503
        code, _ = request(root + route, method="POST",
                          headers={} if expected == 401 else auth, data={})
        assert code == expected, f"{route} returned {code}; expected {expected}"
        results["endpoint_checks"][route] = code
    if not expected_oura:
        code, _ = request(root + "/oauth/callback?code=synthetic-disabled-probe")
        assert code == 503, f"OAuth callback returned {code}; expected disabled (503)"
        results["endpoint_checks"]["/oauth/callback"] = code
    code, _ = request(root + "/google/oauth/callback?code=synthetic-disabled-probe")
    assert code == 503, f"Google callback returned {code}; expected disabled (503)"
    results["endpoint_checks"]["/google/oauth/callback"] = code
    code, info = request(root + "/research/enrollment-information")
    if expected_research:
        frozen = json.loads((Path(__file__).resolve().parents[1]
                             / "addon/research_enrollment.json").read_text("utf-8"))
        assert code == 200 and info.get("bundle_hash") == frozen["sha256"], (
            "The live enrollment documents do not match the release"
        )
        assert info.get("version") == frozen["bundle"]["version"]
        assert info.get("notice_hash") == payload.get("notice_hash")
        assert info.get("signature_method") == "explicit_consent"
    else:
        assert code == 503, "Enrollment must remain disabled"
    results["endpoint_checks"]["enrollment-information"] = code
    # Exercises the hosted function -> private RPC -> DB connection without
    # creating measurements. This leaves a synthetic deletion tombstone/receipt,
    # preventing a lost registration response from resurrecting that identity.
    deletion = {"receipt_secret": "r" * 43}
    code, receipt = request(
        root + "/research/participant", method="DELETE", headers=auth, data=deletion
    )
    assert code == 200 and receipt.get("complete") is True, "Private deletion RPC is unavailable"
    results["endpoint_checks"]["delete_nonexistent_participant"] = code
    if check_quota:
        # Same absent capability identity; no registration or research data.
        # At most 21 requests also covers crossing one fixed-minute boundary.
        for _ in range(20):
            code, _, response_headers = request(
                root + "/research/participant",
                method="DELETE",
                headers=auth,
                data=deletion,
                return_headers=True,
            )
            if code == 429:
                retry = int(response_headers.get("Retry-After", "0"))
                assert 0 < retry <= 86400, "Quota response lacks a valid Retry-After"
                results["endpoint_checks"]["deletion_actor_quota"] = code
                results["quota_retry_after_seconds"] = retry
                break
            assert code == 200, f"Deletion quota probe returned unexpected {code}"
        else:
            raise AssertionError("Deletion actor quota did not reject repeated requests")
    if publishable_key:
        parts = urlsplit(root)
        api = parts.scheme + "://" + parts.netloc + "/rest/v1"
        headers = {"apikey": publishable_key}
        code, _ = request(
            api + "/rpc/qs_register",
            method="POST",
            headers=headers,
            data={"p_id": str(uuid.uuid4()), "p_hash": "a" * 64},
        )
        assert code in (401, 403), f"Anonymous client reached private register RPC ({code})"
        results["endpoint_checks"]["anonymous_register_denied"] = code
        for name, params in (
            ("qs_admit", {"p_family": "delete", "p_subject": "a" * 64}),
            ("qs_ops_record", {"p_route": "delete", "p_status": 200, "p_duration_ms": 1}),
        ):
            code, _ = request(api + "/rpc/" + name, method="POST", headers=headers, data=params)
            assert code in (401, 403), f"Anonymous client reached {name} ({code})"
            results["endpoint_checks"]["anonymous_" + name + "_denied"] = code
        code, _ = request(
            api + "/submissions?select=id&limit=1",
            headers={**headers, "Accept-Profile": "qs_private"},
        )
        assert code in (401, 403, 406), f"Private schema is exposed ({code})"
        results["endpoint_checks"]["private_schema_denied"] = code
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service_url")
    parser.add_argument("--publishable-key", help="Optional public key for anonymous REST probes")
    parser.add_argument("--expect-oura-enabled", action="store_true")
    parser.add_argument("--expect-research-enabled", action="store_true")
    parser.add_argument(
        "--check-quota",
        action="store_true",
        help="Also exercise the absent-identity deletion quota (up to 21 no-op requests)",
    )
    arguments = parser.parse_args()
    print(
        json.dumps(
            check(
                arguments.service_url,
                publishable_key=arguments.publishable_key,
                check_quota=arguments.check_quota,
                expected_oura=arguments.expect_oura_enabled,
                expected_research=arguments.expect_research_enabled,
            ),
            indent=2,
        )
    )
