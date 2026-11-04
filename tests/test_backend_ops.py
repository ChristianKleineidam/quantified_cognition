"""Monitoring fails visibly; an older source never erases independent deletions."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from tools.backend_ops import assess, merge_ledger
from tools.silentworker.health_notifications import NEW, OLD, patch


def healthy(now):
    return (
        dict.fromkeys(("oura_enabled", "google_health_enabled", "research_enabled"), False),
        {
            "server_time": now,
            "maintenance_at": now,
            "probe_checked_at": now,
            "research_maintenance_at": now,
            "probe_ok": True,
            "alerts": [],
        },
        {"last_ledger_at": now.isoformat(), "last_backup_at": now.isoformat(), "status": "healthy"},
    )


def test_monitor_detects_independent_outages_and_each_stale_heartbeat():
    now = datetime.now(timezone.utc)
    status, database, backup = healthy(now)
    assert assess(status, database, backup, now) == []
    assert "public_status_unreachable" in assess(None, database, backup, now)
    assert "database_unreachable" in assess(status, None, backup, now)
    for field in ("maintenance_at", "probe_checked_at", "research_maintenance_at"):
        changed = {**database, field: now - timedelta(minutes=13)}
        assert field + "_stale" in assess(status, changed, backup, now)
    assert "feature_state_unexpected" in assess(
        {**status, "research_enabled": True}, database, backup, now
    )
    assert "backup_stale" in assess(
        status, database, {**backup, "last_backup_at": (now - timedelta(days=2)).isoformat()}, now
    )
    assert "research_deletion_overdue" in assess(
        status,
        {**database, "alerts": ["research_deletion_overdue"]},
        backup,
        now,
    )


def test_deletion_ledger_survives_a_source_rollback_and_rejects_missing_history(tmp_path):
    path = tmp_path / "deletions.json"
    identity = "00000000-0000-4000-8000-000000000001"
    deletions = {identity: datetime.now(timezone.utc).isoformat()}
    assert merge_ledger(path, deletions, existing_files=False) == (deletions, True)
    assert merge_ledger(path, {}, existing_files=True) == (deletions, False)
    assert json.loads(path.read_text())["deletions"] == deletions
    path.unlink()
    with pytest.raises(ValueError, match="ledger is missing"):
        merge_ledger(path, {}, existing_files=True)


def test_notification_patch_preserves_other_rules_and_the_existing_target():
    before = "- id: 'example'\n  alias: silentworker health problem notification\n" + OLD
    actions = "  actions:\n  - action: notify.send_message\n    target: {device_id: unchanged}\n"
    unrelated = "- id: 'other'\n  alias: unrelated\n"
    expected = before.replace(OLD, NEW) + actions + unrelated
    assert patch(before + actions + unrelated) == (expected, True)
    assert patch(expected) == (expected, False)
    with pytest.raises(ValueError):
        patch(before.replace("conditions: []", "conditions: [custom]") + actions + unrelated)
