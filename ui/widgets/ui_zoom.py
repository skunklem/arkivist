# ui_zoom.py
from __future__ import annotations
from typing import Iterable, Set, Type
from PySide6.QtCore import QObject
from PySide6.QtGui import QFont, QFontInfo, QKeySequence, QAction
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QToolButton, QCheckBox, QRadioButton,
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit,
    QGroupBox, QMenuBar, QMenu, QStatusBar, QToolBar, QTreeView, QListView, QTableView,
    QAbstractItemView, QTextEdit, QPlainTextEdit
)

class UiZoom(QObject):
    """
    UI-only font zoom controller.
    - Scales fonts on common 'chrome' widgets (labels, buttons, trees, menus, etc.).
    - Skips rich text editors (QPlainTextEdit/QTextEdit) so your existing editor zoom still rules.
    - Adds menu items + shortcuts to an existing View menu (self.viewMenu).
    """

    # Widgets we WANT to scale
    _INCLUDE_CLASSES: Set[Type[QWidget]] = {
        QLabel, QPushButton, QToolButton, QCheckBox, QRadioButton,
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit,
        QGroupBox, QMenuBar, QMenu, QStatusBar, QToolBar,
        QTreeView, QListView, QTableView,
    }

    # Widgets we EXCLUDE (your editors keep their own zoom)
    _EXCLUDE_CLASSES: Set[Type[QWidget]] = { QPlainTextEdit, QTextEdit }

    def __init__(self, *, base_pt: float | None = None, parent: QObject | None = None):
        super().__init__(parent)
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("UiZoom requires a running QApplication.")

        # Detect starting point size from app font, unless caller specifies.
        self._base_pt = float(base_pt) if base_pt is not None else max(8.0, QFontInfo(app.font()).pointSizeF() or 10.0)
        self._scale = 1.0

    # ---- Public API ----
    def attach_menu(self, view_menu, *, add_shortcuts: bool = True) -> None:
        """Attach UI Zoom actions to an existing View menu (self.viewMenu)."""
        zoom_in = QAction("UI Text Bigger", self)
        zoom_out = QAction("UI Text Smaller", self)
        zoom_reset = QAction("Reset UI Text", self)

        if add_shortcuts:
            zoom_in.setShortcut(QKeySequence("Ctrl+Alt++"))    # avoids clashing with your editor zoom
            zoom_in.setShortcut(QKeySequence("Ctrl+Alt+="))
            zoom_out.setShortcut(QKeySequence("Ctrl+Alt+-"))
            zoom_reset.setShortcut(QKeySequence("Ctrl+Alt+0"))

        zoom_in.triggered.connect(lambda: self.nudge(+1))
        zoom_out.triggered.connect(lambda: self.nudge(-1))
        zoom_reset.triggered.connect(self.reset)

        view_menu.addSeparator()
        view_menu.addAction(zoom_in)
        view_menu.addAction(zoom_out)
        view_menu.addAction(zoom_reset)

    def nudge(self, steps: int) -> None:
        """Increase/decrease UI font ~5% per step."""
        # scale grows ~5% per step; clamp 75–175%
        factor = 1.05 ** steps
        self._scale = min(1.75, max(0.75, self._scale * factor))
        self.apply()

    def reset(self) -> None:
        self._scale = 1.0
        self.apply()

    def set_base_pt(self, pt: float) -> None:
        """If you want to redefine the baseline point size used for UI chrome."""
        self._base_pt = float(pt)
        self.apply()

    def apply(self) -> None:
        """Apply scaled font to chrome widgets and refresh affected views."""
        app = QApplication.instance()
        if app is None:
            return

        target_pt = max(6.0, min(24.0, self._base_pt * self._scale))
        # IMPORTANT: do NOT touch the app font here to avoid changing editor baselines.
        # If you prefer one-knob global zoom, swap this block for: app.setFont(QFont(app.font().family(), round(target_pt)))

        # IMPORTANT: do NOT touch the app font here to avoid changing editor baselines.
        base = QFont(app.font())
        base.setPointSizeF(target_pt)

        for w in QApplication.allWidgets():
            if not self._should_scale_widget(w):
                continue

            # keep weight/italic; just bump size
            f = QFont(w.font())
            f.setPointSizeF(target_pt)
            w.setFont(f)

            # re-polish so metrics/layout recompute
            s = w.style()
            s.unpolish(w); s.polish(w)

            if isinstance(w, QAbstractItemView):
                # Recompute row metrics and repaint only the viewport
                try:
                    w.reset()  # forces a fresh layout pass
                except Exception:
                    pass
                w.updateGeometry()
                vp = w.viewport()
                if vp is not None:
                    vp.update()
                    vp.repaint()  # immediate paint is OK on a rare zoom event
            else:
                w.updateGeometry()
                # Avoid QListWidget.update() overload issues by using repaint()
                w.repaint()

    # ---- Helpers ----
    def _should_scale_widget(self, w: QWidget) -> bool:
        # Exclude editors (your current zoom handles them)
        for t in self._EXCLUDE_CLASSES:
            if isinstance(w, t):
                return False
        # Include known chrome classes
        for t in self._INCLUDE_CLASSES:
            if isinstance(w, t):
                return True
        return False
