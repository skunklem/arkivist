# ui/widgets/common.py
from __future__ import annotations
from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QLabel, QFrame, QSizePolicy

def _blend(a: QColor, b: QColor, t: float) -> QColor:
    """Linear blend between two colors: 0 -> a, 1 -> b."""
    return QColor(
        int(a.red()   + (b.red()   - a.red())   * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue()  + (b.blue()  - a.blue())  * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )

class StatusLine(QLabel):
    """
    A small framed status pill that stays readable in light/dark themes.
    Call one of the helpers to set state:
      - show_neutral("Viewing …")
      - set_dirty()                   # shows a red 'unsaved' pill
      - set_saved_now()               # brief green 'saved' pulse then neutral
      - show_info("…")
      - show_error("…")
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusPill")  # ADD

        # Look & feel
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setContentsMargins(0, 0, 0, 0)

        # Make it look like a small embedded status element
        self.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.setLineWidth(1)
        self.setMidLineWidth(0)
        self.setMinimumWidth(0)           # allow pill to hug text

        # Make it look like a pill
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # Internal timer for auto-reverting transient states
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._revert_to_neutral)

        # Cache last neutral message so we can revert to it
        self._neutral_text = "Ready"

        # Start neutral
        self._apply_neutral_style()
        self.setText(self._neutral_text)

    # ---------- Public API ----------

    def show_neutral(self, text: str = "Ready"):
        self._timer.stop()
        self._neutral_text = text or "Ready"
        self._apply_neutral_style()
        self.setText(self._neutral_text)

    def set_dirty(self, text: str = "● Unsaved changes"):
        self._timer.stop()
        self._apply_state_style(kind="dirty")
        self.setText(text)

    def set_saved_now(self, text: str = "✓ Saved"):
        # Show a short green confirmation then revert to neutral text
        self._timer.stop()
        self._apply_state_style(kind="saved")
        self.setText(text)
        self._timer.start(1350)  # ~1.35s pulse

    def show_info(self, text: str):
        self._timer.stop()
        self._apply_state_style(kind="info")
        self.setText(text)

    def show_error(self, text: str):
        self._timer.stop()
        self._apply_state_style(kind="error")
        self.setText(text)

    def set_ok(self):    self._apply_variant("StatusPill--ok")
    def set_warn(self):  self._apply_variant("StatusPill--warn")
    def set_err(self):   self._apply_variant("StatusPill--err")

    # ---------- Internals ----------

    def _apply_variant(self, cls: str):
        # drop previous variants
        cur = self.property("class") or ""
        kept = " ".join([c for c in cur.split() if not c.startswith("StatusPill--")])
        self.setProperty("class", (kept + " " + cls).strip())
        self.style().unpolish(self); self.style().polish(self); self.update()

    def _revert_to_neutral(self):
        self._apply_neutral_style()
        self.setText(self._neutral_text)

    def _apply_neutral_style(self):
        pal = self.palette()
        base = pal.color(QPalette.Window)        # background of panel
        txt  = pal.color(QPalette.WindowText)    # normal text
        # Subtle tinted background for separation
        bg   = _blend(base, txt, 0.06)           # 6% toward text for contrast
        border = _blend(base, txt, 0.35)

        self.setStyleSheet(f"""
            QLabel {{
                color: {txt.name()};
                background-color: {bg.name()};
                border: 1px solid {border.name()};
                border-radius: 4px;
                padding: 2px 8px;
            }}
        """)

    def _apply_state_style(self, kind: str):
        pal   = self.palette()
        base  = pal.color(QPalette.Window)
        txt   = pal.color(QPalette.WindowText)

        if kind == "dirty":
            fg = QColor("#B22222")  # firebrick
            bg = QColor("#FFF0F0")
            br = fg
        elif kind == "saved":
            fg = QColor("#1B6E1B")
            bg = QColor("#E9F7E9")
            br = fg
        elif kind == "info":
            fg = QColor("#1A4F85")
            bg = QColor("#EAF2FB")
            br = fg
        elif kind == "error":
            fg = QColor("#8B0000")
            bg = QColor("#FDECEC")
            br = fg
        else:
            # Fallback to neutral
            self._apply_neutral_style()
            return

        # Blend with theme base to keep it gentle in dark mode
        bg = _blend(base, bg, 0.85)
        # Ensure text meets theme contrast reasonably
        fg = _blend(txt, fg, 0.75)
        br = _blend(txt, br, 0.75)

        self.setStyleSheet(f"""
            QLabel {{
                color: {fg.name()};
                background-color: {bg.name()};
                border: 1px solid {br.name()};
                border-radius: 4px;
                padding: 2px 8px;
                font-weight: 500;
            }}
        """)

    # Defensive: stop timer if widget is going away
    def hideEvent(self, ev):
        self._timer.stop()
        super().hideEvent(ev)

    def closeEvent(self, ev):
        self._timer.stop()
        super().closeEvent(ev)



class EditStateController(QObject):
    stateChanged = Signal(str)   # "Viewing" | "Editing"

    def __init__(self, initial="Viewing", parent=None):
        super().__init__(parent)
        self._state = initial

    @property
    def state(self):
        return self._state

    def set_viewing(self):
        self._set("Viewing")

    def set_editing(self):
        self._set("Editing")

    def _set(self, val):
        if self._state != val:
            self._state = val
            self.stateChanged.emit(val)
