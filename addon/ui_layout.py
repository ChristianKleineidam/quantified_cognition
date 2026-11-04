"""Keep report controls reachable on smaller screens and at larger text scales."""

from aqt.qt import QFrame, QLabel, QLayout, QScrollArea, Qt, QVBoxLayout, QWidget


def measurement_limitations(parent):
    note = QLabel(
        "Quantified Cognition uses new, experimental methods to estimate learning and recall "
        "from your Anki history. Their reliability for detecting changes in everyday cognitive "
        "performance has not yet been established.\n\n"
        "Use the results to spot patterns and inform personal experiments or adjustments to "
        "your routines, alongside other evidence and your own experience. They should not be "
        "the sole basis for major decisions. Quantified Cognition does not diagnose medical "
        "conditions or provide treatment advice.\n\n"
        "More observations and narrower uncertainty ranges do not eliminate possible bias "
        "or establish that the measurements are valid.",
        parent,
    )
    note.setWordWrap(True)
    note.setTextFormat(Qt.TextFormat.PlainText)
    return note


def scrollable_report(dialog, width, height, name):
    outer = QVBoxLayout(dialog)
    scroll = QScrollArea(dialog)
    scroll.setAccessibleName(name)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    content = QWidget()
    body = QVBoxLayout(content)
    body.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
    body.setContentsMargins(0, 0, 0, 0)
    scroll.setWidget(content)
    outer.addWidget(scroll, 1)
    available = dialog.screen().availableGeometry()
    dialog.resize(min(width, available.width() - 40), min(height, available.height() - 80))
    dialog.setMinimumSize(min(760, available.width() - 40), min(450, available.height() - 80))
    return body, outer, scroll
