"""Keep report controls reachable on smaller screens and at larger text scales."""

from aqt.qt import QFrame, QLayout, QScrollArea, QVBoxLayout, QWidget


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
