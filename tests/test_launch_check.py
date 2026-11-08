import pytest

from addon.enrollment import load_bundle
from tools import check_deployment


@pytest.mark.parametrize("research", [False, True])
def test_launch_verifier_checks_state_without_registering_or_uploading(monkeypatch, research):
    calls = []
    frozen = load_bundle()
    def fake(url, *, method="GET", headers=None, data=None, **kwargs):
        calls.append((url, method, headers))
        if url.endswith("/status"):
            return 200, {"oura_enabled": True, "google_health_enabled": False,
                         "research_enabled": research, "operations_version": 1,
                         "research_controls_version": 1, "notice_hash": "synthetic"}
        if url.endswith("/enrollment-information"):
            return (200, {"bundle_hash": frozen["sha256"],
                          "version": frozen["bundle"]["version"],
                          "notice_hash": "synthetic", "signature_method": "explicit_consent"}
                    ) if research else (503, {})
        if method == "DELETE":
            return 200, {"complete": True}
        if "/research/" in url and research:
            assert headers == {}
            return 401, {}
        return 503, {}
    monkeypatch.setattr(check_deployment, "request", fake)
    check_deployment.check("https://synthetic.test/qs-service", expected_oura=True,
                           expected_research=research)
    assert not any("/oauth/" in url and "/google/" not in url for url, _, _ in calls)
    assert all(not headers or "Authorization" not in headers
               for url, method, headers in calls if method == "POST" and research
               and "/research/" in url)
