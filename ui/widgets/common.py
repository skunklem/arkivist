# ui/widgets/common.py
from __future__ import annotations
import re, weakref
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QEvent, QUrl, QPoint, QRect, QMargins, QSize, QDateTime
from PySide6.QtGui import QPalette, QColor, QCursor, QPainter
from PySide6.QtWidgets import (
    QLabel, QFrame, QSizePolicy, QToolTip, QDialog, QTextBrowser,
    QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget, QPushButton,
    QApplication, QGraphicsDropShadowEffect, QWidget
)

from ui.widgets.helpers import parse_internal_url

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


class AliasPicker(QDialog):
    def __init__(self, conn, project_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose item to alias to")
        self.conn = conn
        self.pid = project_id
        self.sel_wid = None
        v = QVBoxLayout(self)
        self.edit = QLineEdit(self); self.edit.setPlaceholderText("Type to filter…")
        self.list = QListWidget(self)
        btns = QHBoxLayout()
        ok = QPushButton("OK"); cancel = QPushButton("Cancel")
        btns.addWidget(ok); btns.addWidget(cancel)
        v.addWidget(self.edit); v.addWidget(self.list); v.addLayout(btns)
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        self.edit.textChanged.connect(self._refilter)
        self._load_all()

    def _load_all(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, title, kind FROM world_items WHERE project_id=? AND COALESCE(deleted,0)=0 ORDER BY title", (self.pid,))
        self._rows = cur.fetchall()
        self._refilter()

    def _refilter(self):
        q = (self.edit.text() or "").strip().lower()
        self.list.clear()
        for wid, title, kind in self._rows:
            t = (title or "")
            if q and q not in t.lower():
                continue
            self.list.addItem(f"{t}  —  {kind}  (#{wid})")

    def accept(self):
        item = self.list.currentItem()
        if item:
            txt = item.text()
            m = re.search(r"#(\d+)\)$", txt)
            if m:
                self.sel_wid = int(m.group(1))
        super().accept()


class _OneShotClickEater(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            QApplication.instance().removeEventFilter(self)
            return True
        return False


class _HoverCardPopup(QFrame):
    def __init__(self, mw):
        super().__init__(mw)
        self.setWindowFlags(Qt.ToolTip)  # hoverable & doesn't steal focus
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        # single thin edge, no shadow, tight padding
        self.setStyleSheet("QFrame { background: palette(Base); border: 1px solid palette(Mid); border-radius: 6px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)   # was 8
        self.setGraphicsEffect(None)
        self.view = QTextBrowser(self)
        self.view.setOpenExternalLinks(False)
        self.view.setOpenLinks(False)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setStyleSheet("QTextBrowser { background: transparent; }")
        lay.addWidget(self.view)
        mw._apply_doc_styles(self.view)
        self.view.anchorClicked.connect(self._on_popup_anchor_clicked)
        self._inside = False
        self.view.viewport().installEventFilter(self)
        # theme-aware link css
        mw._apply_doc_styles(self.view)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Enter:
            self._inside = True
        elif ev.type() == QEvent.Leave:
            self._inside = False
        return False

    def show_card(self, html: str, at_global: QPoint):
        # inject link CSS into the popup too
        self.view.setHtml(html)
        self.adjustSize()
        # self.move(at_global + QPoint(12, 12))
        self.move(at_global + QPoint(6, 6))
        self.show()

    def _on_popup_anchor_clicked(self, qurl: QUrl):
        # squelch anchor routing
        try:
            self.parent()._squelch_anchor_until_ms = QDateTime.currentMSecsSinceEpoch() + 450
        except Exception:
            pass

        # eat the very next click so underlying view never sees it
        eater = _OneShotClickEater(self)
        QApplication.instance().installEventFilter(eater)

        # Hide card first so it doesn't cover dialogs/panels
        self.hide()
        # Route click through the app
        self.parent().route_anchor_click(qurl)

    def enterEvent(self, e):
        self._inside = True
        super().enterEvent(e)
    def leaveEvent(self, e):
        self._inside = False
        super().leaveEvent(e)

def _anchor_left_global(vp: QWidget, tb: QTextBrowser,
                        href: str, pos_vp: QPoint) -> QPoint:
    """
    pos_vp is viewport-relative. We scan left on the same y to find the first pixel
    that still returns this href, then return that point in GLOBAL coords.
    """
    y = pos_vp.y()
    x = pos_vp.x()
    # scan left (cap at 300 px to keep it cheap)
    min_x = max(0, x - 300)
    left_x = x
    while left_x > min_x:
        probe = QPoint(left_x - 1, y)
        if tb.anchorAt(probe) != href:
            break
        left_x -= 1
    # map the found viewport point to global
    return vp.mapToGlobal(QPoint(left_x, y))

# helper (place above the class)
def _scan_anchor_bounds_global(vp: QWidget, tb: QTextBrowser,
                               href: str, pos_vp: QPoint) -> QRect:
    """Find horizontal bounds of hovered anchor on this line; return GLOBAL rect."""
    y = pos_vp.y()
    # LEFT scan
    left = pos_vp.x()
    while left > 0 and tb.anchorAt(QPoint(left - 1, y)) == href:
        left -= 1
    # RIGHT scan
    right = pos_vp.x()
    vpw = vp.width()
    while right < vpw - 1 and tb.anchorAt(QPoint(right + 1, y)) == href:
        right += 1
    # Get line height from the exact cursor line under pos_vp (viewport coords)
    cur = tb.cursorForPosition(pos_vp)
    line_rect_vp = tb.cursorRect(cur)
    rect_vp = QRect(QPoint(left, line_rect_vp.top()),
                           QSize(max(1, right - left + 1), line_rect_vp.height()))
    return QRect(vp.mapToGlobal(rect_vp.topLeft()), rect_vp.size())



class _WikiHoverFilter(QObject):
    def __init__(self, mw, text_browser: QTextBrowser):
        super().__init__(mw)
        self.mw = mw
        self._hover_debug = False

        # weakrefs to avoid touching deleted C++ objects
        self._tb_ref = weakref.ref(text_browser)
        vp = text_browser.viewport()
        self._vp_ref = weakref.ref(vp) if vp else (lambda: None)

        self.card = _HoverCardPopup(mw)
        self._current_href = None

        # timers
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_if_outside)

        # install filters on objects that actually emit events
        text_browser.installEventFilter(self)      # focus/hide/mouse press on TB
        vp.installEventFilter(self)                # mouse move/hover on viewport

        # hide if TB destroyed
        text_browser.destroyed.connect(self.card.hide)

        # hide on app deactivation (Alt+Tab)
        QApplication.instance().applicationStateChanged.connect(
            lambda st: self.card.hide() if st != Qt.ApplicationActive else None
        )

        # global mouse move fallback (so we can hide even if TB stops sending events)
        QApplication.instance().installEventFilter(self)

    # --------- small helpers ----------

    def _tb_alive(self):
        tb = self._tb_ref() if self._tb_ref else None
        if tb is None:
            return None
        # Touch metaObject to see if C++ is gone
        try:
            _ = tb.metaObject()
        except RuntimeError:
            return None
        return tb

    def _vp_alive(self):
        vp = self._vp_ref() if self._vp_ref else None
        if vp is None:
            return None
        try:
            _ = vp.metaObject()
        except RuntimeError:
            return None
        return vp

    def _anchor_under_cursor(self) -> str:
        vp = self._vp_alive()
        tb = self._tb_alive()
        if not vp or not tb:
            self._dbg("tb/vp dead in _anchor_under_cursor; hiding")
            self.card.hide()
            return ""
        try:
            pt = vp.mapFromGlobal(QCursor.pos())
            return tb.anchorAt(pt)
        except RuntimeError:
            self._dbg("anchorAt runtime error; hiding")
            self.card.hide()
            return ""

    def _over_anchor(self) -> bool:
        return bool(self._anchor_under_cursor())

    def _dbg(self, *a):
        if self._hover_debug:
            print("[hover]", *a)

    # --------- hide/show policy ----------

    def _hide_if_outside(self):
        if not self.card.isVisible():
            return

        cursor = QCursor.pos()
        cg = self.card.geometry()

        if cg.contains(cursor):
            return
        if self._over_anchor():
            return

        ar = getattr(self, "_anchor_rect_global", None)
        if ar and not ar.isNull():
            # column width = overlap of x-intervals, bounded below
            left_x  = max(ar.left(),  cg.left())
            right_x = min(ar.right(), cg.right())
            col_w   = max(10, right_x - left_x)   # at least 10 px
            if col_w > 0:
                # column from just below anchor to just above card
                col_top = ar.bottom() - 1
                col_h   = max(1, cg.top() - col_top + 2)
                column  = QRect(QPoint(left_x, col_top), QSize(col_w, col_h))
                if column.contains(cursor):
                    return

        self.card.hide()

    # --------- main event filter ----------

    def eventFilter(self, obj, ev):
        et = ev.type()

        # If the TB has been deleted, bail early
        tb = self._tb_alive()
        vp = self._vp_alive()

        # Global mouse move: if card visible and we’re over *any* anchor, refresh even if the event didn’t come from the viewport yet
        if ev.type() == QEvent.MouseMove and self.card.isVisible():
            href_now = self._anchor_under_cursor()
            if href_now:
                if href_now != self._current_href:
                    self._current_href = href_now
                    qurl = QUrl(href_now)
                    info = parse_internal_url(qurl)
                    if info:
                        html = self.mw._hover_card_html_for(qurl)
                        tb = self._tb_alive(); vp = self._vp_alive()
                        if html and tb and vp:
                            # recompute anchor rect and re-place the card right away
                            pos_vp = vp.mapFromGlobal(QCursor.pos())
                            ar = _scan_anchor_bounds_global(vp, tb, href_now, pos_vp)
                            self._anchor_rect_global = ar
                            self.card.show_card(html, QPoint(ar.left(), ar.bottom() - 5))
                # don’t hide; we’re over an anchor
                return False
    
        # Global mouse move: if card visible and we're not over card or anchor, hide.
        if et == QEvent.MouseMove and self.card.isVisible():
            if not self.card.geometry().contains(QCursor.pos()) and not self._over_anchor():
                self._dbg("global mouse -> hide (not over card/anchor)")
                self.card.hide()
            return False

        # TB-level events
        if tb and obj is tb:
            if et in (QEvent.FocusOut, QEvent.Hide, QEvent.Leave):
                self._dbg("tb focus/hide/leave -> arm hide")
                self._hide_timer.start(180)
            elif et == QEvent.MouseButtonPress:
                self._dbg("tb mouse press -> hide immediately")
                self.card.hide()
            return False

        # Viewport mouse/hover move
        if vp and obj is vp and et in (QEvent.MouseMove, QEvent.HoverMove):
            try:
                # use viewport-relative position for anchorAt
                pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
                href = tb.anchorAt(pos) if tb else ""
            except RuntimeError:
                self._dbg("vp move but tb dead -> hide")
                self.card.hide()
                return False

            if href:
                self._hide_timer.stop()
                if href != self._current_href or not self.card.isVisible():
                    print("href changed or card hidden:", href, self._current_href, self.card.isVisible())
                    self._current_href = href
                    qurl = QUrl(href)
                    info = parse_internal_url(qurl)
                    if info:
                        html = self.mw._hover_card_html_for(qurl)
                        if html:
                            # 1) get exact anchor rect in GLOBAL coords
                            ar = _scan_anchor_bounds_global(vp, tb, href, pos)
                            self._anchor_rect_global = ar

                            # 2) place the card at anchor.bottomLeft, with -2px vertical overlap (closer!)
                            card_at = QPoint(ar.left(), ar.bottom() - 5)
                            self.card.show_card(html, card_at)
                            self._dbg("show_card at", card_at, "anchorRect", self._anchor_rect_global)
                            self._dbg(f"show_card href={href}")
                return False

            # left anchor: grace hide (even if still inside pane)
            self._dbg("left anchor -> start hide timer")
            self._hide_timer.start(180)
            return False

        return False
