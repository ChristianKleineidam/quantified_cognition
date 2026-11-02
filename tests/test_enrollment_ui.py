"""Exercise the actual two-step dialog and verify that work stays off Qt's thread."""

import threading

import pytest

pytest.importorskip("aqt")
from test_threading import app as thread_app  # noqa: E402
from test_threading import pump_until  # noqa: E402
from test_threading import runtime as thread_runtime  # noqa: E402

from addon import enrollment_ui  # noqa: E402
from addon.research import SharingConfig, sharing_state  # noqa: E402
from addon.service import ServiceConfig, ServiceError  # noqa: E402

app = thread_app
runtime = thread_runtime


def test_eligibility_then_explicit_signature_enrollment_does_not_send_measurements(
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
            "signature": "Test signature",
        }
        calls.append(True)
        return {"receipt": {"enrollment_id": "synthetic"}}

    monkeypatch.setattr(enrollment_ui, "enrollment_information", information)
    monkeypatch.setattr(enrollment_ui, "enroll", enroll)
    dialog = enrollment_ui.EnrollmentDialog(
        main, source, ServiceConfig("https://example.org"), sharing
    )
    dialog.show()
    pump_until(app, lambda: not dialog.busy)
    assert not dialog.next.isEnabled() and not dialog.join.isEnabled()
    assert not dialog.consent.isVisible()
    dialog.adult.setChecked(True)
    assert not dialog.next.isEnabled()
    dialog.own.setChecked(True)
    assert dialog.next.isEnabled()
    dialog.show_consent()
    assert dialog.consent.isVisible()
    assert dialog.statement.text() == dialog.frozen["bundle"]["statement"]
    assert not dialog.join.isEnabled()
    dialog.signature.setText("Test signature")
    assert not dialog.join.isEnabled()
    dialog.explicit.setChecked(True)
    assert dialog.join.isEnabled() and not dialog.join.isDefault()
    dialog.sign()
    pump_until(app, lambda: dialog.enrolled)
    assert calls == [True]
    assert "No measurements were uploaded" in dialog.status.text()
    assert not dialog.join.isEnabled()
    assert dialog.save_record.isEnabled()
    assert sharing_state(source, sharing).metadata() == first.metadata()
    assert sharing_state(source, sharing).first_exposure is None
    dialog.close()


def test_closed_service_still_allows_information_copy_and_personal_use(runtime, app, monkeypatch):
    main, _ = runtime

    def closed(*_args):
        raise ServiceError("Research enrollment is not open.")

    monkeypatch.setattr(enrollment_ui, "enrollment_information", closed)
    monkeypatch.setattr(enrollment_ui, "enroll", lambda *_a, **_k: pytest.fail("closed enrollment"))
    dialog = enrollment_ui.EnrollmentDialog(main, main.col.path, ServiceConfig(), SharingConfig())
    dialog.show()
    pump_until(app, lambda: not dialog.busy)
    assert "not open" in dialog.status.text()
    assert "JollyJoyJourney" in dialog.documents.toPlainText()
    dialog.adult.setChecked(True)
    dialog.own.setChecked(True)
    assert not dialog.next.isEnabled()
    assert dialog.save_info.isEnabled()
    dialog.close()
