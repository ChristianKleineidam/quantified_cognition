"""Profile-local comparisons with the last displayed insights visit."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import asdict
from datetime import date, datetime, timezone

VERSION = 1
FILENAME = "personal-insights-visits.json"
FORMATION = ("formation", "formation_short_term", "formation_long_term")
PROVIDERS = ("oura", "google_health")


def _health_days(health, provider):
    """Hash measurements/classification/timing, excluding import-only metadata."""
    sessions = {}
    for session in health.sessions:
        if session.provider == provider:
            sessions.setdefault(session.day, []).append(
                [
                    session.start,
                    session.end,
                    session.kind,
                    session.status,
                    {k: v for k, v in session.values.items() if v is not None},
                    session.algorithm,
                    session.wake_day,
                ]
            )
    out = {}
    ignored = {
        f"{provider}_{field}" for field in ("imported_at_utc", "synced_at_utc", "data_version")
    }
    for day, record in health.days.items():
        if not record.values.get(f"{provider}_day_basis"):
            continue
        values = {
            k: v
            for k, v in record.values.items()
            if k.startswith(provider + "_") and k not in ignored and v is not None
        }
        periods = sorted(
            json.dumps(s, sort_keys=True, allow_nan=False)
            for s in sessions.get(day.isoformat(), [])
        )
        out[day.isoformat()] = hashlib.sha256(
            json.dumps([values, periods], sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
    return out


def make_snapshot(
    result, report, health, *, method_version, as_of, start=None, end=None, period_days=None
):
    """Counts and day hashes only: no review pairs, exact times or raw health data."""
    study = {}
    for day in report.days if report else ():
        if day.day >= as_of:
            continue
        metrics = {}
        for name in FORMATION:
            metric = getattr(day, name)
            ready = bool(
                metric
                and metric.score_pp is not None
                and math.isfinite(metric.score_pp)
                and metric.count >= report.settings.minimum_reviews
                and metric.outcome_days >= report.settings.minimum_formation_outcome_days
                and metric.uncertainty_low_pp is not None
                and (as_of - day.day).days >= result.settings.formation_age_days
                and day.formation.coverage_percent >= result.settings.formation_coverage
            )
            metrics[name] = (
                {
                    "count": metric.count,
                    "cohort": metric.cohort_count,
                    "pending": metric.pending_count,
                    "excluded": metric.excluded_count,
                    "ready": ready,
                }
                if metric
                else None
            )
        study[day.day.isoformat()] = metrics
    estimates = {}
    for key, value in result.associations.items():
        estimates["association/" + key] = dict(
            scope="nap" if key.startswith("nap_") else "main",
            status=value.status,
            days=value.days,
            low=value.low,
            high=value.high,
            point=value.correlation,
        )
    for key, value in result.phase_associations.items():
        estimates["phase/" + key] = dict(
            scope=key.split("/")[0],
            status=value.status,
            days=value.days,
            low=value.low_pp,
            high=value.high_pp,
            point=value.effect_pp,
        )
    return dict(
        version=VERSION,
        provider=result.provider,
        as_of=as_of.isoformat(),
        analysis=dict(
            method=method_version,
            score_method=report.calculation_version if report else 0,
            settings=asdict(result.settings),
            memory={
                **result.memory_metadata,
                "timezone": {
                    "names": list(time.tzname),
                    "standard_offset": time.timezone,
                    "dst_offset": time.altzone,
                    "tz_env": os.environ.get("TZ", ""),
                },
            },
            window=(
                {"kind": "rolling", "days": period_days}
                if period_days
                else {
                    "kind": "fixed",
                    "start": start.isoformat() if start else None,
                    "end": end.isoformat() if end else None,
                }
            ),
        ),
        study=study,
        health=_health_days(health, result.provider),
        estimates=estimates,
    )


def _path(source):
    from .storage import results_directory

    return results_directory(source) / FILENAME


def _read(source):
    path = _path(source)
    empty = {
        "version": VERSION,
        "counter": 0,
        "providers": {},
        "epochs": {},
        "id": uuid.uuid4().hex,
    }
    if not path.exists():
        return empty, ""
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError()
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("version") != VERSION
            or type(value.get("counter")) is not int
            or value["counter"] < 0
            or not isinstance(value.get("providers"), dict)
        ):
            raise ValueError()
        value.setdefault("epochs", {})
        value.setdefault("id", uuid.uuid4().hex)
        if not isinstance(value["id"], str) or len(value["id"]) != 32:
            raise ValueError()
        if not isinstance(value["epochs"], dict) or any(
            type(v) is not int or v < 0 for v in value["epochs"].values()
        ):
            raise ValueError()
        return value, ""
    except (OSError, ValueError):
        return empty, "Previous visit history could not be read; this visit starts a new baseline."


def _valid_snapshot(value, provider):
    try:
        if (
            not isinstance(value, dict)
            or value["version"] != VERSION
            or value["provider"] != provider
            or type(value["ticket"]) is not int
            or type(value["revision"]) is not int
            or value["ticket"] < 1
            or value["revision"] < 1
            or datetime.fromisoformat(value["viewed_at_utc"]).tzinfo is None
            or not all(
                isinstance(value[k], dict) for k in ("analysis", "study", "health", "estimates")
            )
        ):
            return False
        if not all(
            isinstance(value["analysis"].get(k), dict) for k in ("settings", "memory", "window")
        ):
            return False
        for day, data in value["study"].items():
            date.fromisoformat(day)
            for metric in FORMATION:
                row = data[metric]
                if row is not None and (
                    type(row["ready"]) is not bool
                    or any(
                        type(row[k]) is not int or row[k] < 0
                        for k in ("count", "cohort", "pending", "excluded")
                    )
                ):
                    return False
        for day, digest in value["health"].items():
            date.fromisoformat(day)
            if not isinstance(digest, str) or len(digest) != 64:
                return False
        for estimate in value["estimates"].values():
            if (
                estimate["scope"] not in ("main", "nap")
                or estimate["status"] not in ("exploratory estimate", "insufficient data")
                or type(estimate["days"]) is not int
                or estimate["days"] < 0
            ):
                return False
            for key in ("point", "low", "high"):
                number = estimate[key]
                if number is not None and (
                    type(number) not in (int, float) or not math.isfinite(number)
                ):
                    return False
            if estimate["status"] == "exploratory estimate" and (
                estimate["low"] is None
                or estimate["high"] is None
                or estimate["low"] > estimate["high"]
            ):
                return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def begin_visit(source, provider, state=None):
    """Reserve an ordered ticket; never advance the displayed-results baseline."""
    from .insights import _atomic_json
    from .storage import profile_job_lock

    if provider not in PROVIDERS:
        raise ValueError("Invalid insights provider.")
    with profile_job_lock(source, "visits"):
        value, problem = _read(source)
        epoch = value["epochs"].get(provider, 0)
        if (
            state
            and not problem
            and state.get("store_id") == value["id"]
            and state.get("epoch") == epoch
            and value["counter"] >= state.get("ticket", 0)
        ):
            return state
        previous = value["providers"].get(provider)
        if previous is not None and not _valid_snapshot(previous, provider):
            previous = None
            value["providers"].pop(provider, None)
            problem = "Previous visit history could not be read; this visit starts a new baseline."
        value["counter"] = max(value["counter"], previous["ticket"] if previous else 0) + 1
        _atomic_json(_path(source), value)
        return {
            "ticket": value["counter"],
            "previous": previous,
            "problem": problem,
            "epoch": epoch,
            "store_id": value["id"],
        }


def _context_changes(old, new):
    reasons = []
    for key, label in (
        ("method", "analysis method changed"),
        ("score_method", "memory calculation method changed"),
        ("settings", "analysis settings changed"),
        ("window", "time window changed"),
    ):
        if old.get(key) != new.get(key):
            reasons.append(label)
    om, nm = old.get("memory", {}), new.get("memory", {})
    if om.get("parameters_hash") != nm.get("parameters_hash"):
        reasons.append("FSRS settings changed")
    for fields, label in (
        (("minimum_reviews", "minimum_formation_outcome_days"), "score thresholds changed"),
        (("rollover_hour", "timezone"), "day boundary or timezone changed"),
        (("anki_version",), "Anki version changed"),
    ):
        if any(om.get(key) != nm.get(key) for key in fields):
            reasons.append(label)
    return reasons


EXPORT_FIELDS = (
    "previous_visit_at_utc",
    "visit_comparison",
    "previous_low",
    "previous_high",
    "previous_matched_days",
    "uncertainty_change",
    "uncertainty_width_change_percent",
    "visit_added_study_days",
    "visit_added_health_days",
    "visit_resolved_pending_outcomes",
)


def export_changes(changes, key):
    if not changes:
        return {"visit_comparison": "not compared"}
    if changes.get("first_visit"):
        return {"visit_comparison": "first snapshot"}
    row = {
        "previous_visit_at_utc": changes["previous_at"],
        "visit_comparison": "; ".join(changes["not_comparable"]) or "comparable",
        "visit_added_study_days": changes["added_study_days"],
        "visit_added_health_days": changes["added_health_days"],
        "visit_resolved_pending_outcomes": changes["resolved_pending"],
    }
    value = changes["estimates"].get(key)
    if value:
        row.update(
            previous_low=value["old"]["low"],
            previous_high=value["old"]["high"],
            previous_matched_days=value["old"]["days"],
            uncertainty_change=value["trend"],
            uncertainty_width_change_percent=value["width_percent"],
        )
    return row


def compare_visits(current, previous, *, problem=""):
    if not previous:
        return {"first_visit": True, "problem": problem}
    if not _valid_snapshot(previous, current["provider"]):
        return {"first_visit": True, "problem": "Previous visit history could not be read."}
    old_days, new_days = previous["study"], current["study"]
    old_health, new_health = previous["health"], current["health"]
    added = set(new_days) - old_days.keys()
    health_added = set(new_health) - old_health.keys()
    old_last = max(old_days, default="")
    old_health_last = max(old_health, default="")
    result = dict(
        first_visit=False,
        previous_at=previous["viewed_at_utc"],
        added_study_days=len(added),
        backfilled_study_days=sum(d <= old_last for d in added),
        added_health_days=len(health_added),
        backfilled_health_days=sum(d <= old_health_last for d in health_added),
        updated_health_days=sum(
            old_health[d] != new_health[d] for d in old_health.keys() & new_health.keys()
        ),
        removed_study_days=len(old_days.keys() - new_days.keys()),
        removed_health_days=len(old_health.keys() - new_health.keys()),
        resolved_pending=0,
        followup_days=0,
        short_followups=0,
        long_followups=0,
        no_pending_days=0,
        revised_formation_days=0,
        newly_ready={name: 0 for name in FORMATION},
        estimates={},
    )
    old_analysis, new_analysis = previous["analysis"], current["analysis"]
    readiness_same = (
        old_analysis.get("memory") == new_analysis.get("memory")
        and old_analysis.get("method") == new_analysis.get("method")
        and old_analysis.get("score_method") == new_analysis.get("score_method")
        and all(
            old_analysis.get("settings", {}).get(k) == new_analysis["settings"].get(k)
            for k in ("formation_age_days", "formation_coverage")
        )
    )
    for day in old_days.keys() & new_days.keys():
        old, new = old_days[day], new_days[day]
        a, b = old["formation"], new["formation"]
        stable = bool(a and b and a["cohort"] == b["cohort"] and a["excluded"] == b["excluded"])
        if stable:
            resolved = a["pending"] - b["pending"]
            if resolved > 0 and b["count"] - a["count"] == resolved:
                result["resolved_pending"] += resolved
                result["followup_days"] += 1
                result["no_pending_days"] += b["pending"] == 0
                if all(old[name] and new[name] for name in FORMATION[1:]):
                    short, long = (
                        new[name]["count"] - old[name]["count"] for name in FORMATION[1:]
                    )
                    if short >= 0 and long >= 0 and short + long == resolved:
                        result["short_followups"] += short
                        result["long_followups"] += long
            elif resolved < 0 or b["count"] < a["count"]:
                result["revised_formation_days"] += 1
        elif a != b:
            result["revised_formation_days"] += 1
        if readiness_same:
            for name in FORMATION:
                if old[name] and new[name] and not old[name]["ready"] and new[name]["ready"]:
                    result["newly_ready"][name] += 1
    reasons = _context_changes(old_analysis, new_analysis)
    result["not_comparable"] = reasons
    result["rolling_window"] = new_analysis.get("window", {}).get("kind") == "rolling"
    if not reasons:
        for key, new in current["estimates"].items():
            old = previous["estimates"].get(key)
            if old is None:
                continue
            before = old["status"] == "exploratory estimate"
            after = new["status"] == "exploratory estimate"
            change = {"old": old, "new": new, "width_percent": None}
            if before and after:
                ow, nw = old["high"] - old["low"], new["high"] - new["low"]
                tolerance = max(1e-8, 1e-4 * max(ow, nw))
                change["trend"] = (
                    "narrower"
                    if nw < ow - tolerance
                    else "wider"
                    if nw > ow + tolerance
                    else "unchanged"
                )
                if ow > 1e-8:
                    change["width_percent"] = 100 * (nw - ow) / ow
            else:
                change["trend"] = (
                    "new estimate"
                    if after
                    else "no longer available"
                    if before
                    else "still insufficient"
                )
            result["estimates"][key] = change
    return result


def save_displayed_visit(source, snapshot, state, revision):
    """File-only publication, protected against stale workers and provider deletion."""
    from .insights import _atomic_json
    from .storage import profile_job_lock

    with profile_job_lock(source, "visits"):
        value, _ = _read(source)
        if state.get("store_id") != value["id"]:
            return False
        if state.get("epoch") != value["epochs"].get(snapshot["provider"], 0):
            return False
        existing = value["providers"].get(snapshot["provider"])
        if existing and not _valid_snapshot(existing, snapshot["provider"]):
            existing = None
        if existing and (existing["ticket"], existing["revision"]) >= (
            state["ticket"],
            revision,
        ):
            return False
        record = dict(
            snapshot,
            ticket=state["ticket"],
            revision=revision,
            viewed_at_utc=snapshot.get("viewed_at_utc") or datetime.now(timezone.utc).isoformat(),
        )
        value["counter"] = max(value["counter"], state["ticket"])
        value["providers"][snapshot["provider"]] = record
        _atomic_json(_path(source), value)
    return True


def queue_displayed_visit(source, snapshot, state, revision):
    """An owned worker finishes a queued checkpoint even after the window closes."""
    future = Future()
    snapshot = dict(snapshot, viewed_at_utc=datetime.now(timezone.utc).isoformat())

    def run():
        try:
            future.set_result(save_displayed_visit(source, snapshot, state, revision))
        except Exception as error:
            future.set_exception(error)

    threading.Thread(target=run, name="qs-insights-visit", daemon=False).start()
    return future


def delete_visit_history(source, provider=None):
    from .insights import _atomic_json
    from .storage import profile_job_lock

    with profile_job_lock(source, "visits"):
        if _path(source).exists():
            value, _ = _read(source)
            for selected in PROVIDERS if provider is None else (provider,):
                value["providers"].pop(selected, None)
                value["epochs"][selected] = value["epochs"].get(selected, 0) + 1
            _atomic_json(_path(source), value)


def overview_changes(changes, *, scope="main", phases=True):
    if changes.get("first_visit", True):
        return (
            changes.get("problem")
            or "First visit: future visits will compare with the results shown here."
        )
    visited = datetime.fromisoformat(changes["previous_at"]).astimezone().date().isoformat()
    text = (
        f"Since your last visit ({visited}): {changes['added_study_days']:,} added study days, "
        f"{changes['added_health_days']:,} added health days"
    )
    backfilled = changes["backfilled_study_days"] + changes["backfilled_health_days"]
    if backfilled:
        text += (
            f" (including {changes['backfilled_study_days']:,} historical study and "
            f"{changes['backfilled_health_days']:,} health days)"
        )
    if changes["updated_health_days"]:
        text += f"; {changes['updated_health_days']:,} existing health days updated"
    text += "."
    if changes["resolved_pending"]:
        text += (
            f"\nEarlier study days: {changes['resolved_pending']:,} fewer pending formation "
            f"outcomes across {changes['followup_days']:,} days"
        )
        if changes["short_followups"] + changes["long_followups"] == changes["resolved_pending"]:
            text += (
                f" (≤30 days: {changes['short_followups']:,}; "
                f">30 days: {changes['long_followups']:,})"
            )
        text += "."
    ready = changes["newly_ready"]
    if any(ready.values()):
        text += (
            f"\nNewly eligible formation days: overall {ready['formation']:,}, "
            f"≤30 days {ready['formation_short_term']:,}, "
            f">30 days {ready['formation_long_term']:,}."
        )
    if (
        changes["removed_study_days"]
        or changes["removed_health_days"]
        or changes["revised_formation_days"]
    ):
        text += (
            f"\nHistory revisions: {changes['removed_study_days']:,} study / "
            f"{changes['removed_health_days']:,} health days removed; "
            f"{changes['revised_formation_days']:,} earlier formation days revised."
        )
    if changes["not_comparable"]:
        text += "\nUncertainty not compared: " + "; ".join(changes["not_comparable"]) + "."
    else:
        prefix = "phase/" if phases else "association/"
        selected = [
            c
            for key, c in changes["estimates"].items()
            if key.startswith(prefix) and c["new"]["scope"] == scope
        ]
        counts = {
            trend: sum(c["trend"] == trend for c in selected)
            for trend in ("narrower", "wider", "new estimate", "no longer available", "unchanged")
        }
        if not any(counts[t] for t in ("narrower", "wider", "new estimate", "no longer available")):
            text += (
                f"\nUncertainty ranges unchanged for {counts['unchanged']} comparable estimates."
                if counts["unchanged"]
                else "\nUncertainty cannot yet be compared: "
                "these estimates still have insufficient data."
            )
        else:
            text += (
                f"\nUncertainty ranges: {counts['narrower']} narrower, {counts['wider']} wider; "
                f"{counts['new estimate']} newly available estimates"
            )
            if counts["no longer available"]:
                text += f"; {counts['no longer available']} now insufficient"
            text += "."
        if changes["rolling_window"]:
            text += " The rolling window also drops older dates."
        text += " Changes also reflect which days qualify."
    return text


def comparison_history(changes, key):
    if not changes or changes.get("first_visit") or changes.get("not_comparable"):
        return ""
    change = changes["estimates"].get(key)
    if not change:
        return ""
    if change["trend"] == "new estimate":
        return " Previously insufficient data; an estimate is now available."
    if change["old"]["status"] != "exploratory estimate":
        return ""
    old = change["old"]
    text = f" Last visit: {old['low']:+.2f} to {old['high']:+.2f}, {old['days']:,} matched days."
    if change["trend"] in ("narrower", "wider"):
        percent = change["width_percent"]
        text += (
            f" Range width is {abs(percent):.0f}% {change['trend']}."
            if percent is not None
            else f" The range is {change['trend']}; its previous width was zero."
        )
    elif change["trend"] == "no longer available":
        text += " The current data no longer supports an estimate."
    else:
        text += " Range width is unchanged."
    return text
