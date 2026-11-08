import json
import sys
import time

import pytest

from addon import oura_research
from addon.service import ServiceConfig, ServiceError
from tools.research_guard import run_guarded

CONFIG = ServiceConfig("https://example.org/service")


@pytest.fixture
def secrets(monkeypatch):
    values = {"oura": {"access_token": "access", "refresh_token": "refresh",
                       "authorization_proof": "server-proof", "expires_at": time.time()+10000,
                       "service": CONFIG.base_url},
              "research": {"id": "participant", "secret": "research-secret"}}
    monkeypatch.setattr(oura_research, "read_secret", lambda p, slot: values.get(slot))
    monkeypatch.setattr(oura_research, "write_secret", lambda p, slot, v: values.update({slot: v}))
    return values


def test_monitor_starts_only_on_confirmed_oura_upload_and_keeps_deletion_receipt(
    tmp_path, monkeypatch, secrets
):
    calls = []
    monkeypatch.setattr(
        oura_research, "call_service", lambda *a, **k: calls.append((a, k)) or {"state": "active"}
    )
    source = tmp_path / "collection.anki2"
    oura_research.renew_authorization(source, CONFIG)
    assert not calls and not secrets["research"].get("oura_monitoring")
    oura_research.renew_authorization(source, CONFIG, required=True)
    packet = calls[-1][0][2]
    assert packet["authorization_proof"] == "server-proof" and packet["access_token"] == "access"
    assert "refresh_token" not in packet and len(packet["receipt_secret"]) >= 43
    assert secrets["research"]["deletion_secret"] == packet["receipt_secret"]
    oura_research.renew_authorization(source, CONFIG)
    assert calls[-1][0][2]["receipt_secret"] == packet["receipt_secret"]


def test_monitor_never_renews_after_local_withdrawal_or_when_oura_is_disconnected(
    tmp_path, monkeypatch, secrets
):
    monkeypatch.setattr(
        oura_research, "call_service", lambda *_a, **_k: pytest.fail("must not transmit")
    )
    secrets["research"]["delete_pending"] = True
    oura_research.renew_authorization(tmp_path / "collection.anki2", CONFIG)
    with pytest.raises(ServiceError):
        oura_research.renew_authorization(tmp_path / "collection.anki2", CONFIG, required=True)
    secrets["research"].pop("delete_pending")
    secrets["oura"] = None
    with pytest.raises(ServiceError, match="Reconnect"):
        oura_research.renew_authorization(tmp_path / "collection.anki2", CONFIG, required=True)


def test_persisted_receipt_survives_binding_response_loss(tmp_path, monkeypatch, secrets):
    def offline(*a, **k):
        raise ServiceError("Lost response")
    monkeypatch.setattr(oura_research, "call_service", offline)
    with pytest.raises(ServiceError):
        oura_research.renew_authorization(tmp_path / "collection.anki2", CONFIG, required=True)
    assert secrets["research"]["oura_monitoring"] and secrets["research"]["deletion_secret"]
    assert "access_token" not in json.dumps(secrets["research"])


def test_guard_refuses_offline_work_and_stops_a_running_synthetic_job(tmp_path):
    marker = tmp_path / "ran.txt"
    command = [sys.executable, "-c", f"open({str(marker)!r},'w').write('ran')"]
    assert run_guarded(command, lambda: False, interval=0.05) == 2
    assert not marker.exists()
    checks = []
    def allowed():
        checks.append(1)
        return len(checks) == 1
    started = time.monotonic()
    command = [sys.executable, "-c", "import time;time.sleep(60)"]
    assert run_guarded(command, allowed, interval=0.1) == 2
    assert time.monotonic()-started < 10
