"""Exercise inline enrollment and verify that work stays off Qt's thread."""

import threading

import pytest

pytest.importorskip("aqt")
from test_threading import app as thread_app  # noqa: E402
from test_threading import pump_until  # noqa: E402
from test_threading import runtime as thread_runtime  # noqa: E402

from addon import sharing_ui  # noqa: E402
from addon.research import SharingConfig, sharing_state  # noqa: E402
from addon.service import ServiceConfig, ServiceError  # noqa: E402

app = thread_app
runtime = thread_runtime


def test_eligibility_then_explicit_consent_does_not_send_measurements(
    runtime, app, monkeypatch
):
    main, _ = runtime
    source = main.col.path
    sharing = SharingConfig()
    first = sharing_state(source, sharing, event_type="sharing_choice", choice="opt_in")
    calls = []

    def information(*_args):
        assert threading.get_ident() != main.owner
        return {"version": "synthetic"}

    def enroll(*args, **kwargs):
        assert threading.get_ident() != main.owner
        assert kwargs == {
            "adult": True,
            "own_data": True,
            "explicit_consent": True,
        }
        calls.append("enroll")
        return {"receipt": {"enrollment_id": "synthetic"}}

    def prepare(*args, **kwargs):
        assert threading.get_ident() != main.owner
        calls.append("prepare")
        return {"package": {"submission_id": "synthetic", "rows": [["2026-11-01"]]}}, {
            "operator": "Synthetic study",
            "privacy_url": "https://example.org/privacy",
        }

    def send(*args):
        assert threading.get_ident() != main.owner
        calls.append(args[-1])
        return {"submission_id": "synthetic"}

    monkeypatch.setattr(sharing_ui, "enrollment_information", information)
    monkeypatch.setattr(sharing_ui, "enroll", enroll)
    monkeypatch.setattr(sharing_ui, "prepare_submission", prepare)
    monkeypatch.setattr(sharing_ui, "upload_submission", send)
    dialog = sharing_ui.SharingDialog(
        main, source, first, ServiceConfig("https://example.org"), manage=True
    )
    dialog.show()
    pump_until(app, lambda: not dialog.busy)
    assert dialog.enrollment_form.isVisible()
    assert not dialog.join.isEnabled() and not dialog.send.isVisible()
    assert not dialog.consent.isVisible()
    dialog.adult.setChecked(True)
    assert not dialog.consent.isVisible()
    dialog.own.setChecked(True)
    assert dialog.consent.isVisible()
    assert dialog.statement.text() == dialog.frozen["bundle"]["statement"]
    assert not dialog.join.isEnabled()
    assert not hasattr(dialog, "signature")
    assert dialog.join.text() == "Agree and join research"
    dialog.explicit.setChecked(True)
    assert dialog.join.isEnabled() and not dialog.join.isDefault()
    dialog.agree()
    pump_until(app, lambda: dialog.enrolled and not dialog.busy)
    assert calls == ["enroll", "prepare"]
    assert "Synthetic study" in dialog.preview.text()
    assert not dialog.enrollment_form.isVisible()
    assert dialog.save_record.isVisible()
    assert dialog.send.isEnabled() and not dialog.send.isDefault()
    dialog.send.click()
    pump_until(app, lambda: not dialog.busy)
    assert calls == ["enroll", "prepare", "synthetic"]
    assert "Package sent" in dialog.status.text()
    assert sharing_state(source, sharing).metadata() == first.metadata()
    assert sharing_state(source, sharing).first_exposure is None
    dialog.close()


def test_closed_service_still_allows_information_copy_and_personal_use(runtime, app, monkeypatch):
    main, _ = runtime

    def closed(*_args):
        raise ServiceError("Research enrollment is not open.")

    snapshot = sharing_state(main.col.path, event_type="sharing_choice", choice="opt_in")
    monkeypatch.setattr(sharing_ui, "enrollment_information", closed)
    monkeypatch.setattr(sharing_ui, "enroll", lambda *_a, **_k: pytest.fail("closed enrollment"))
    dialog = sharing_ui.SharingDialog(main, main.col.path, snapshot, manage=True)
    dialog.show()
    pump_until(app, lambda: not dialog.busy)
    assert "not open" in dialog.status.text()
    assert "JollyJoyJourney" in dialog.details.toPlainText()
    assert not dialog.enrollment_form.isVisible() and not dialog.send.isVisible()
    assert not dialog.save_info.isVisible()
    dialog.details_button.click()
    assert dialog.save_info.isVisible() and dialog.save_info.isEnabled()
    dialog.close()
